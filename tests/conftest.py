"""Test-wide settings: provide a deterministic legal-data encryption key.

Without this, app/core/encryption.py would fall back to a SECRET_KEY-derived
key (removed — forbidden), or fail. Setting the env var here runs before any
test module imports the app, and pydantic-settings gives os.environ precedence
over the repo .env file.
"""

import base64
import os

os.environ.setdefault(
    "LEGAL_DATA_ENCRYPTION_KEY",
    base64.urlsafe_b64encode(b"A" * 32).decode("ascii"),
)

# CI（GitHub Actions）无 .env 文件：注入占位密钥/Key，使 Settings 校验通过。
# 本地开发不受影响（CI 环境变量仅在 runner 上存在；真实密钥不会被 setdefault 覆盖）。
if os.environ.get("CI"):
    os.environ.setdefault("SECRET_KEY", "test-secret-key-" + "x" * 24)
    os.environ.setdefault("LLM_API_KEY", "test-llm-api-key")

# E-2b 门禁：签署/开放 API 默认关闭，回归测试需要全流程可用，
# 因此在测试环境显式打开（真实密钥/URL 不会被 setdefault 覆盖）。
os.environ.setdefault("SIGNING_FADADA_SANDBOX_URL", "https://sandbox.fadada.test")
os.environ.setdefault("SIGNING_FADADA_API_KEY", "test-api-key-" + "x" * 24)
os.environ.setdefault("OPEN_API_ENABLED", "1")

# E-7：测试使用独立向量库目录，避免仓库 chroma_db 存量数据（schema 随版本演进）
# 或并发测试相互污染。
import tempfile  # noqa: E402

os.environ.setdefault(
    "CHROMA_PERSIST_DIR",
    os.path.join(tempfile.gettempdir(), "aibg_test_chroma"),
)

# #90：评测 bundle 导出到临时目录，避免测试写仓库内跟踪文件（eval/bundles/feedback_autogen/qa_dataset.json）
os.environ.setdefault(
    "EVAL_BUNDLE_OUTPUT_DIR",
    os.path.join(tempfile.gettempdir(), "aibg_test_eval_bundle"),
)

# CI 无 data/ 目录：为 OCR 测试生成占位 PNG fixture（本地产物不入 git）。
def _ensure_upload_fixtures() -> None:
    import io
    from pathlib import Path

    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return
    upload_dir = Path(__file__).resolve().parent.parent / "data" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    for name in ("contract.png", "scan.png"):
        target = upload_dir / name
        if target.exists():
            continue
        with Image.new("RGB", (64, 64), color=(200, 200, 200)) as image:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            target.write_bytes(buffer.getvalue())


_ensure_upload_fixtures()


# RAG① 嵌入缓存单例跨测试共享，会污染 patch(llm_client.embed) 的调用次数断言，
# 每个测试前/后清空内存缓存。
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_rag_embedding_cache():
    from app.services.rag_cache import rag_embedding_cache
    rag_embedding_cache.clear()
    yield
    rag_embedding_cache.clear()


@pytest.fixture(autouse=True)
def _force_heuristic_rerank_in_tests():
    """BGE/LLM 重排依赖外部模型/网络，单元测试统一走启发式（快速、确定性）。"""
    from unittest.mock import patch
    from app.core.config import get_settings
    from app.services.rag_service import rag_service
    rag_service._reranker = None  # 懒重建，按当前（被 patch 的）引擎选择
    settings = get_settings()
    with patch.object(settings, "RAG_RERANK_ENGINE", "heuristic"), patch.object(
        settings, "RAG_LLM_RERANK_ENABLED", False,
    ):
        yield
    rag_service._reranker = None
