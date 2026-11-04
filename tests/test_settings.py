"""Настройки из окна: data/settings.json перекрывает config.py, сохраняются только отличия."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config
from src.ui.app import normalize_line


class SettingsTests(unittest.TestCase):
    def test_coerce(self):
        self.assertEqual(config.coerce("ALLOWED_EXTENSIONS", "pdf, .DOCX"), [".pdf", ".docx"])
        self.assertEqual(config.coerce("ALLOWED_EXTENSIONS", ""), [])
        self.assertEqual(config.coerce("DOWNLOAD_MIN_DELAY", "2,5"), 2.5)
        self.assertIs(config.coerce("ENABLE_DOWNLOADS", "false"), False)
        with self.assertRaises(ValueError):
            config.coerce("DOWNLOAD_WORKERS", "три")

    def test_save_only_differences_and_reload(self):
        before = config.current_settings()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(config, "SETTINGS_FILE", path):
                try:
                    config.save_settings({**before, "DOWNLOAD_WORKERS": "5", "ALLOWED_EXTENSIONS": "pdf, docx"})
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                                     {"DOWNLOAD_WORKERS": 5, "ALLOWED_EXTENSIONS": [".pdf", ".docx"]})
                    self.assertEqual(config.DOWNLOAD_WORKERS, 5)
                    config.DOWNLOAD_WORKERS = 1
                    config._load_settings_file()          # так config читает файл при запуске процесса
                    self.assertEqual(config.DOWNLOAD_WORKERS, 5)
                finally:
                    config.save_settings(dict(config.DEFAULTS))
                    vars(config).update(before)

    def test_process_line_to_window_format(self):
        self.assertEqual(normalize_line("[2026-11-04 09:43:29,561] [INFO] --- ПРОГРЕСС: 1/2 (50.0%)"),
                         "[09:43:29] --- ПРОГРЕСС: 1/2 (50.0%)")
        self.assertEqual(normalize_line("[2026-11-04 09:43:29,561] [ERROR] сбой"), "[09:43:29] [ERROR] сбой")
        self.assertTrue(normalize_line("Traceback (most recent call last):").endswith("Traceback (most recent call last):"))


if __name__ == "__main__":
    unittest.main()
