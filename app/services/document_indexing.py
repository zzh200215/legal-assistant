from collections.abc import Callable


def build_embedding_id(document_id: int, chunk_index: int) -> str:
    return f"doc{document_id}_chunk{chunk_index}"


def prepare_chunks_for_indexing(
    document_id: int,
    chunks: list[dict],
    *,
    build_visual_summary: Callable[..., str],
) -> list[dict]:
    prepared = []
    for chunk in chunks:
        visual_summary = build_visual_summary(
            visual_tags=chunk.get("visual_tags"),
            segment_type=chunk.get("segment_type"),
            page_number=chunk.get("page_number"),
            ocr_quality=chunk.get("ocr_quality"),
            section_title=chunk.get("section_title"),
        )
        prepared.append(
            {
                **chunk,
                "embedding_id": chunk.get("embedding_id") or build_embedding_id(document_id, chunk["chunk_index"]),
                "index_content": f"{visual_summary}\n{chunk['content']}".strip() if visual_summary else chunk["content"],
                "visual_evidence": chunk.get("visual_evidence") or "",
                "visual_region": chunk.get("visual_region"),
            }
        )
    return prepared
