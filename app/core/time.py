"""Time helpers compatible with the project's legacy naive-UTC columns."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current UTC time without tzinfo for existing SQL datetime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
