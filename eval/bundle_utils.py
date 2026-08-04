import json
from pathlib import Path


EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST_PATH = EVAL_DIR / "corpus_manifest.json"
DEFAULT_DATASET_PATH = EVAL_DIR / "qa_dataset.json"
DEFAULT_MATRIX_PATH = EVAL_DIR / "experiment_matrix.json"
DEFAULT_OUTPUT_DIR = EVAL_DIR / "outputs"
DEFAULT_BASELINE_SNAPSHOT_PATH = DEFAULT_OUTPUT_DIR / "baseline_snapshot.json"


def resolve_eval_paths(
    *,
    bundle_dir: str | Path | None = None,
    manifest_path: str | Path | None = None,
    dataset_path: str | Path | None = None,
    matrix_path: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Path]:
    resolved_bundle = Path(bundle_dir) if bundle_dir else None
    if resolved_bundle:
        resolved_manifest = Path(manifest_path) if manifest_path else resolved_bundle / "corpus_manifest.json"
        resolved_dataset = Path(dataset_path) if dataset_path else resolved_bundle / "qa_dataset.json"
        resolved_matrix = Path(matrix_path) if matrix_path else resolved_bundle / "experiment_matrix.json"
        resolved_output = Path(output_dir) if output_dir else resolved_bundle / "outputs"
    else:
        resolved_manifest = Path(manifest_path) if manifest_path else DEFAULT_MANIFEST_PATH
        resolved_dataset = Path(dataset_path) if dataset_path else DEFAULT_DATASET_PATH
        resolved_matrix = Path(matrix_path) if matrix_path else DEFAULT_MATRIX_PATH
        resolved_output = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    return {
        "bundle_dir": resolved_bundle,
        "manifest_path": resolved_manifest,
        "dataset_path": resolved_dataset,
        "matrix_path": resolved_matrix,
        "output_dir": resolved_output,
    }


def load_bundle_meta(bundle_dir: str | Path | None) -> dict:
    if not bundle_dir:
        return {}
    meta_path = Path(bundle_dir) / "bundle_meta.json"
    if not meta_path.exists():
        return {}
    with meta_path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}
