import unittest
from unittest.mock import patch

from app.tasks import create_pilot_backup_task
from scripts.create_pilot_backup import _mysql_dump_command, _postgres_dump_command, _safe_database_label
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

            result = create_pilot_backup_task()

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["message"])


if __name__ == "__main__":
    unittest.main()
