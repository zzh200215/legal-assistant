import unittest

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


if __name__ == "__main__":
    unittest.main()
