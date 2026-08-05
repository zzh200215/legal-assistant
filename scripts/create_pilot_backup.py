"""Create a recoverable pilot backup without exposing database credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_database_label(database_url: str) -> str:
    url = make_url(database_url)
    host = url.host or "local"
    database = url.database or "database"
    return f"{url.drivername}://{host}/{database}"


def _mysql_dump_command(url) -> tuple[list[str], str]:
    config = "\n".join(
        [
            "[client]",
            f"host={url.host or 'localhost'}",
            f"port={url.port or 3306}",
            f"user={url.username or ''}",
            f"password={url.password or ''}",
            "",
        ]
    )
    return ["mysqldump", "--single-transaction", "--routines", "--events", "--set-gtid-purged=OFF", url.database or ""], config


def _postgres_dump_command(url) -> tuple[list[str], dict[str, str]]:
    env = {
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username or "",
        "PGPASSWORD": url.password or "",
    }
    return ["pg_dump", "--format=custom", "--dbname", url.database or ""], env


def _dump_database(database_url: str, output_path: Path) -> str:
    url = make_url(database_url)
    if url.drivername.startswith("mysql"):
        command, config = _mysql_dump_command(url)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as temp:
            temp.write(config)
            temp_path = Path(temp.name)
        try:
            command.insert(1, f"--defaults-extra-file={temp_path}")
            with output_path.open("wb") as target:
                subprocess.run(command, stdout=target, stderr=subprocess.PIPE, check=True, shell=False)
        finally:
            temp_path.unlink(missing_ok=True)
        return "mysql_sql"
    if url.drivername.startswith(("postgresql", "postgres")):
        command, env = _postgres_dump_command(url)
        command.extend(["--file", str(output_path)])
        process_env = os.environ.copy()
        process_env.update(env)
        subprocess.run(command, stderr=subprocess.PIPE, check=True, shell=False, env=process_env)
        return "postgres_custom"
    raise ValueError("Only MySQL and PostgreSQL backup are supported for pilot environments")


def _archive_directories(paths: list[Path], output_path: Path) -> list[str]:
    included = []
    with tarfile.open(output_path, "w:gz") as archive:
        for path in paths:
            if not path.exists() or not path.is_dir():
                continue
            archive.add(path, arcname=path.name, recursive=True)
            included.append(path.name)
    return included


def _prune_old_backups(directory: Path, retention_count: int) -> list[str]:
    """保留最近 retention_count 份 `pilot-backup-*`，删除更旧的。返回被删目录名。"""
    if retention_count <= 0:
        return []
    if not directory.exists() or not directory.is_dir():
        return []
    backups = sorted(
        [path for path in directory.iterdir() if path.is_dir() and path.name.startswith("pilot-backup-")],
        key=lambda path: path.name,  # timestamp 前缀可字典序比较
    )
    removed = []
    for stale in backups[:-retention_count]:
        shutil.rmtree(stale, ignore_errors=True)
        removed.append(stale.name)
    return removed


def create_backup(*, database_url: str, output_dir: Path, data_dirs: list[Path], offsite_dir: Path | None = None, retention_count: int = 0) -> dict:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = output_dir.resolve() / f"pilot-backup-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    url = make_url(database_url)
    dump_name = "database.sql" if url.drivername.startswith("mysql") else "database.dump"
    dump_path = backup_dir / dump_name
    dump_format = _dump_database(database_url, dump_path)
    archive_path = backup_dir / "application-data.tar.gz"
    included_dirs = _archive_directories(data_dirs, archive_path)
    artifacts = [
        {"path": dump_path.name, "sha256": _sha256(dump_path), "size_bytes": dump_path.stat().st_size},
        {"path": archive_path.name, "sha256": _sha256(archive_path), "size_bytes": archive_path.stat().st_size},
    ]
    manifest = {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": _safe_database_label(database_url),
        "database_dump_format": dump_format,
        "included_data_dirs": included_dirs,
        "artifacts": artifacts,
        "restore_requirement": "Restore only into an isolated environment and record the recovery result.",
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # 异地副本（等保差距 #1 异地部分）：目标目录非空时复制整份备份。
    offsite_copy = None
    if offsite_dir:
        target = offsite_dir.resolve() / backup_dir.name
        shutil.copytree(backup_dir, target, dirs_exist_ok=False)
        offsite_copy = str(target)
    # 保留策略（等保运维）：清理本地与异地最旧备份，避免无限累积占盘。
    pruned_local = _prune_old_backups(output_dir.resolve(), retention_count)
    pruned_offsite = _prune_old_backups(offsite_dir.resolve(), retention_count) if offsite_dir else []
    return {
        "backup_dir": str(backup_dir),
        "manifest": manifest,
        "offsite_copy": offsite_copy,
        "pruned": {"local": pruned_local, "offsite": pruned_offsite},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a pilot database and local-data backup.")
    parser.add_argument("--output-dir", default="data/backups", help="Directory for a new timestamped backup folder.")
    parser.add_argument("--database-url-env", default="DATABASE_URL", help="Environment variable containing the database URL.")
    parser.add_argument("--data-dir", action="append", default=["data/uploads", "data/chroma_db"], help="Local directory to archive; may be repeated.")
    parser.add_argument("--offsite-dir", default="", help="Optional offsite target directory; backup is copied there when set.")
    parser.add_argument("--retention-count", type=int, default=0, help="Keep this many newest backups, prune older ones (0=never prune).")
    parser.add_argument("--confirm", action="store_true", help="Required before commands write a backup.")
    args = parser.parse_args()

    database_url = os.getenv(args.database_url_env, "").strip()
    if not database_url:
        print(json.dumps({"status": "error", "message": f"{args.database_url_env} is not set"}))
        return 2
    if not args.confirm:
        print(json.dumps({"status": "confirmation_required", "database": _safe_database_label(database_url)}, ensure_ascii=False))
        return 2
    try:
        result = create_backup(
            database_url=database_url,
            output_dir=Path(args.output_dir),
            data_dirs=[Path(item) for item in args.data_dir],
            offsite_dir=Path(args.offsite_dir) if args.offsite_dir else None,
            retention_count=args.retention_count,
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
