import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from eval.common import ensure_eval_llm_ready
from eval.bundle_utils import DEFAULT_MANIFEST_PATH, resolve_eval_paths
from app.services.document_service import _extract_segments, _split_text
from app.services.rag_service import rag_service


def load_manifest(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError("Corpus manifest must be a list")
    return data


def build_chunks(document_id: int, file_path: Path, file_type: str, chunk_size: int, chunk_overlap: int) -> list[dict]:
    segments = _extract_segments(str(file_path), file_type)
    chunks = _split_text(segments, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return [
        {
            "id": None,
            "chunk_index": chunk["chunk_index"],
            "content": chunk["content"],
            "page_number": chunk.get("page_number"),
            "section_title": chunk.get("section_title"),
            "section_path": chunk.get("section_path"),
            "segment_type": chunk.get("segment_type"),
            "table_like": chunk.get("table_like"),
            "embedding_id": f"doc{document_id}_chunk{chunk['chunk_index']}",
        }
        for chunk in chunks
    ]


def index_corpus(manifest: list[dict], chunk_size: int, chunk_overlap: int) -> list[dict]:
    indexed = []
    for item in manifest:
        file_path = Path(item["file_path"])
        chunks = build_chunks(
            document_id=item["document_id"],
            file_path=file_path,
            file_type=item["file_type"],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        rag_service.index_document(item["document_id"], chunks, user_id=item["user_id"])
        indexed.append(
            {
                "document_id": item["document_id"],
                "document_name": item["document_name"],
                "chunk_count": len(chunks),
                "user_id": item["user_id"],
            }
        )
    return indexed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Index eval fixtures into Chroma for local RAG evaluation")
    parser.add_argument("--bundle-dir", default=None, help="Directory of an eval bundle containing manifest/dataset/matrix")
    parser.add_argument("--manifest", default=None, help="Path to corpus manifest json; overrides bundle manifest when provided")
    parser.add_argument("--chunk-size", type=int, default=800, help="Chunk size used during indexing")
    parser.add_argument("--chunk-overlap", type=int, default=100, help="Chunk overlap used during indexing")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print result json")
    return parser.parse_args()


def main():
    args = parse_args()
    ensure_eval_llm_ready()
    paths = resolve_eval_paths(bundle_dir=args.bundle_dir, manifest_path=args.manifest)
    manifest = load_manifest(paths["manifest_path"])
    result = {
        "config": {
            "chunk_size": args.chunk_size,
            "chunk_overlap": args.chunk_overlap,
            "manifest": str(paths["manifest_path"]),
            "bundle_dir": str(paths["bundle_dir"]) if paths["bundle_dir"] else None,
        },
        "indexed_documents": index_corpus(
            manifest,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        ),
    }
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
