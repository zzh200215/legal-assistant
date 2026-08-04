from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings


class MeetingTranscriptionError(ValueError):
    pass


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    segments: list[dict]


class MeetingTranscriptionService:
    @staticmethod
    @lru_cache(maxsize=1)
    def _model():
        settings = get_settings()
        if not settings.MEETING_ASR_ENABLED:
            raise MeetingTranscriptionError("自动转写未启用，请填写转写文本后再上传")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise MeetingTranscriptionError(
                "未安装自动转写依赖，请安装 faster-whisper，或填写转写文本后再上传"
            ) from exc

        return WhisperModel(
            settings.MEETING_ASR_MODEL,
            device=settings.MEETING_ASR_DEVICE,
            compute_type=settings.MEETING_ASR_COMPUTE_TYPE,
            download_root=settings.MEETING_ASR_DOWNLOAD_ROOT or None,
        )

    def transcribe(self, audio_path: str | Path) -> TranscriptionResult:
        path = Path(audio_path)
        if not path.exists():
            raise MeetingTranscriptionError("音频文件不存在，无法自动转写")

        try:
            raw_segments, _ = self._model().transcribe(
                str(path),
                beam_size=5,
                vad_filter=True,
            )
            segments = []
            for segment in raw_segments:
                text = (segment.text or "").strip()
                if not text:
                    continue
                segments.append(
                    {
                        "start_seconds": round(float(segment.start), 2),
                        "end_seconds": round(float(segment.end), 2),
                        "text": text,
                    }
                )
        except MeetingTranscriptionError:
            raise
        except Exception as exc:
            raise MeetingTranscriptionError("自动转写失败，请填写转写文本后重试") from exc

        text = "\n".join(item["text"] for item in segments).strip()
        if not text:
            raise MeetingTranscriptionError("未从音频中识别到有效文本，请填写转写文本后重试")
        return TranscriptionResult(text=text, segments=segments)


meeting_transcription_service = MeetingTranscriptionService()
