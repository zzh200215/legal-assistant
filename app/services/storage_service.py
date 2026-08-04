from __future__ import annotations

import base64
from pathlib import Path

from app.core.config import get_settings


settings = get_settings()


class LocalStorageService:
    def base_dir(self) -> Path:
        return Path(settings.STORAGE_LOCAL_DIR)

    def ensure_dir(self, base_dir: Path) -> Path:
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir

    def save_bytes(self, *, base_dir: Path, filename: str, content: bytes) -> Path:
        directory = self.ensure_dir(base_dir)
        target = directory / filename
        with open(target, "wb") as f:
            f.write(content)
        return target

    def read_bytes(self, file_path: str | Path) -> bytes:
        with open(file_path, "rb") as f:
            return f.read()

    def to_data_url(self, file_path: str | Path, mime_type: str) -> str:
        encoded = base64.b64encode(self.read_bytes(file_path)).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"


storage_service = LocalStorageService()
