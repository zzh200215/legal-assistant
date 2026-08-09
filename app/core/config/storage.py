"""File / OCR / ASR / backup storage settings."""

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class StorageSettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    STORAGE_PROVIDER: str = "local"
    STORAGE_LOCAL_DIR: str = "./data/uploads"
    # 备份（等保自评差距 #1 整改：每日全量定时备份，见 app/tasks.create_pilot_backup_task）
    BACKUP_OUTPUT_DIR: str = "data/backups"
    BACKUP_DATA_DIRS: list[str] = ["data/uploads", "data/chroma_db"]
    # 异地副本目标目录（等保差距 #1 异地部分）：非空时备份完成后复制一份到此（可指向挂载的对象存储/NAS）。
    BACKUP_OFFSITE_DIR: str = ""
    # 备份保留份数（等保运维：避免每日全量无限累积占盘）：保留最近 N 份，清理更旧的；0=从不清理。
    BACKUP_RETENTION_COUNT: int = Field(default=7, ge=0, le=365)
    # 评测 bundle 导出目录（#90：测试用临时目录，避免写仓库内跟踪文件；空=默认 eval/bundles/feedback_autogen）
    EVAL_BUNDLE_OUTPUT_DIR: str = ""

    # OCR 文档解析
    OCR_ENABLE_IMAGE_PREPROCESS: bool = True
    OCR_PDF_RENDER_DPI: int = 200
    OCR_MIN_TEXT_LENGTH: int = 24
    OCR_MIN_READABLE_RATIO: float = 0.45

    # 会议录音转写（faster-whisper）
    MEETING_ASR_ENABLED: bool = True
    MEETING_ASR_MODEL: str = "small"
    MEETING_ASR_DEVICE: str = "cpu"
    MEETING_ASR_COMPUTE_TYPE: str = "int8"
    MEETING_ASR_DOWNLOAD_ROOT: str = ""
