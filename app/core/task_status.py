from __future__ import annotations

from celery.result import AsyncResult


def serialize_async_result(result: AsyncResult) -> dict:
    payload = {
        "task_id": result.id,
        "state": result.state,
        "ready": result.ready(),
        "successful": result.successful() if result.ready() else False,
    }

    info = result.info
    if result.failed():
        payload["error"] = str(info)
    elif result.successful():
        payload["result"] = info
    elif info:
        payload["info"] = info

    return payload
