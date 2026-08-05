import unittest
from unittest.mock import patch

from app.tasks import create_pilot_backup_task
from scripts.create_pilot_backup import (
    _archive_directories,
    _mysql_dump_command,
    _postgres_dump_command,
    _prune_old_backups,
    _safe_database_label,
)
from sqlalchemy.engine import make_url


class PilotBackupTests(unittest.TestCase):
    def test_safe_database_label_excludes_password(self):
        label = _safe_database_label("mysql+pymysql://lawyer:secret@db.example:3306/legal")

        self.assertEqual(label, "mysql+pymysql://db.example/legal")
        self.assertNotIn("secret", label)

    def test_mysql_dump_uses_temporary_defaults_file_for_credentials(self):
        command, config = _mysql_dump_command(make_url("mysql+pymysql://lawyer:secret@db.example:3306/legal"))

        self.assertEqual(command[0], "mysqldump")
        self.assertNotIn("secret", " ".join(command))
        self.assertIn("password=secret", config)

    def test_postgres_dump_passes_password_through_environment(self):
        command, env = _postgres_dump_command(make_url("postgresql://lawyer:secret@db.example:5432/legal"))

        self.assertEqual(command[0], "pg_dump")
        self.assertNotIn("secret", " ".join(command))
        self.assertEqual(env["PGPASSWORD"], "secret")


class PilotBackupTaskTests(unittest.TestCase):
    def test_beat_schedule_registers_daily_backup(self):
        from app.core.celery_app import celery_app

        entry = celery_app.conf.beat_schedule["create-pilot-backup"]

        self.assertEqual(entry["task"], "create_pilot_backup")
        self.assertEqual(str(entry["schedule"]), "<crontab: 0 2 * * * (m/h/dM/MY/d)>")

    def test_backup_task_skips_sqlite_default_driver(self):
        with patch("app.tasks.get_settings") as mock_settings:
            mock_settings.return_value.DATABASE_URL = "sqlite:///./data/app.db"
            mock_settings.return_value.BACKUP_OUTPUT_DIR = "data/backups"
            mock_settings.return_value.BACKUP_DATA_DIRS = ["data/uploads", "data/chroma_db"]

            result = create_pilot_backup_task()

        self.assertEqual(result["status"], "skipped")
        self.assertIn("sqlite", result["reason"])

    def test_backup_task_reports_subprocess_failure_as_error(self):
        with patch("app.tasks.get_settings") as mock_settings, patch(
            "app.tasks.subprocess.run", side_effect=OSError("mysqldump not found")
        ):
            mock_settings.return_value.DATABASE_URL = "mysql+pymysql://lawyer:secret@db.example:3306/legal"
            mock_settings.return_value.BACKUP_OUTPUT_DIR = "data/backups"
            mock_settings.return_value.BACKUP_DATA_DIRS = ["data/uploads", "data/chroma_db"]
            mock_settings.return_value.BACKUP_OFFSITE_DIR = ""

            result = create_pilot_backup_task()

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["message"])

    def test_create_backup_copies_to_offsite_dir(self):
        import tempfile
        from pathlib import Path

        from scripts.create_pilot_backup import create_backup

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "backups"
            offsite = root / "offsite"
            with patch("scripts.create_pilot_backup._dump_database",
                       side_effect=lambda url, path: path.write_text("dummy") or "mysql_sql"), \
                 patch("scripts.create_pilot_backup._sha256", return_value="d41d8cd98f00b204e9800998ecf8427e"):
                result = create_backup(
                    database_url="mysql+pymysql://lawyer:secret@db.example:3306/legal",
                    output_dir=output,
                    data_dirs=[root / "uploads"],
                    offsite_dir=offsite,
                )
            backup_dir = Path(result["backup_dir"])
            self.assertTrue((backup_dir / "manifest.json").exists())
            self.assertIsNotNone(result["offsite_copy"])
            offsite_copy = Path(result["offsite_copy"])
            self.assertTrue((offsite_copy / "manifest.json").exists())
            self.assertTrue(offsite_copy.name == backup_dir.name)

    def test_prune_old_backups_keeps_newest_and_removes_rest(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dirs = [root / f"pilot-backup-2026080{i}T000000Z" for i in range(5)]
            for d in dirs:
                d.mkdir()
                (d / "manifest.json").write_text("{}")
            root.joinpath("unrelated").mkdir()

            removed = _prune_old_backups(root, retention_count=2)

            self.assertEqual(len(removed), 3)
            self.assertEqual(removed, [dirs[0].name, dirs[1].name, dirs[2].name])
            self.assertTrue(dirs[3].exists())
            self.assertTrue(dirs[4].exists())
            self.assertTrue(root.joinpath("unrelated").exists(), "非备份目录不应被清理")

    def test_prune_old_backups_zero_or_missing_dir(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pilot-backup-20260801T000000Z").mkdir()
            self.assertEqual(_prune_old_backups(root, retention_count=0), [])
            self.assertEqual(_prune_old_backups(root / "does-not-exist", retention_count=5), [])

    def test_retention_forwarded_by_beat_task(self):
        with patch("app.tasks.get_settings") as mock_settings, patch("app.tasks.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"status":"ok","backup_dir":"/x"}'
            mock_settings.return_value.DATABASE_URL = "mysql+pymysql://lawyer:secret@db.example:3306/legal"
            mock_settings.return_value.BACKUP_OUTPUT_DIR = "data/backups"
            mock_settings.return_value.BACKUP_DATA_DIRS = ["data/uploads"]
            mock_settings.return_value.BACKUP_OFFSITE_DIR = "data/offsite"
            mock_settings.return_value.BACKUP_RETENTION_COUNT = 14

            result = create_pilot_backup_task()

            command = mock_run.call_args[0][0]
            self.assertIn("--retention-count", command)
            self.assertEqual(command[command.index("--retention-count") + 1], "14")
            self.assertEqual(result["status"], "ok")

    def test_archive_directories_includes_only_existing(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            present = root / "present"
            present.mkdir()
            included = _archive_directories([present, root / "missing"], root / "out.tar.gz")
            self.assertEqual(included, ["present"])
            self.assertTrue((root / "out.tar.gz").exists())


if __name__ == "__main__":
    unittest.main()
