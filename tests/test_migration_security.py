import unittest
from unittest.mock import MagicMock, patch

from app.core.migration import _ensure_mysql_database_exists


class MigrationSecurityTests(unittest.TestCase):
    def test_ensure_mysql_database_exists_uses_url_object_without_rendering_password(self):
        fake_engine = MagicMock()
        fake_begin = MagicMock()
        fake_engine.begin.return_value = fake_begin
        fake_begin.__enter__.return_value = MagicMock()
        fake_begin.__exit__.return_value = False

        with patch("app.core.migration.create_engine", return_value=fake_engine) as mock_create_engine:
            _ensure_mysql_database_exists("mysql+pymysql://root:secret@localhost:3306/aibg")

        engine_arg = mock_create_engine.call_args.args[0]
        self.assertNotIsInstance(engine_arg, str)
        self.assertEqual(engine_arg.database, "")
        self.assertEqual(engine_arg.password, "secret")
        fake_engine.dispose.assert_called_once()


if __name__ == "__main__":
    unittest.main()
