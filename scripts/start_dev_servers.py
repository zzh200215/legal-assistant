from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
LOG_DIR = ROOT / "logs"


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.CREATE_NO_WINDOW
    )


def _start(name: str, cmd: list[str], cwd: Path) -> int:
    LOG_DIR.mkdir(exist_ok=True)
    stdout = (LOG_DIR / f"{name}.out.log").open("ab")
    stderr = (LOG_DIR / f"{name}.err.log").open("ab")
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=stdout,
        stderr=stderr,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=_creation_flags(),
    )
    return process.pid


def main() -> None:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("npm was not found in PATH")

    backend_pid = _start(
        "backend",
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8001",
        ],
        ROOT,
    )
    frontend_pid = _start(
        "frontend",
        [npm, "run", "dev", "--", "--host=127.0.0.1", "--port=5173"],
        FRONTEND,
    )
    worker_pid = _start(
        "celery_worker",
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.core.celery_app.celery_app",
            "worker",
            "--loglevel=info",
            "-Q",
            "document,notification,billing,connector",
            "--concurrency=4",
        ],
        ROOT,
    )
    network_pid = _start(
        "celery_worker_network",
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.core.celery_app.celery_app",
            "worker",
            "--loglevel=info",
            "-Q",
            "llm,connector",
            "--concurrency=2",
        ],
        ROOT,
    )
    beat_pid = _start(
        "celery_beat",
        [sys.executable, "-m", "celery", "-A", "app.core.celery_app.celery_app", "beat", "--loglevel=info"],
        ROOT,
    )
    print(f"backend pid: {backend_pid}")
    print(f"frontend pid: {frontend_pid}")
    print(f"celery worker pid: {worker_pid}")
    print(f"celery worker_network pid: {network_pid}")
    print(f"celery beat pid: {beat_pid}")


if __name__ == "__main__":
    main()
