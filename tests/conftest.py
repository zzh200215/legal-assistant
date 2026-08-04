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
