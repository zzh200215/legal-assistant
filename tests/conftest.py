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
