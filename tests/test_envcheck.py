import tempfile
import unittest
from importlib import metadata
from pathlib import Path
from unittest import mock

from src import envcheck


class EnvCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.req = Path(self.tmp.name) / "requirements.txt"
        self.req.write_text("PyMuPDF==1.26.7\nmissing-lib==1.0  # comment\n-r other.txt\n", encoding="utf-8")
        for name, value in (("REQUIREMENTS", self.req), ("PYTHON_VERSION", Path(self.tmp.name) / "none")):
            patcher = mock.patch.object(envcheck, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(envcheck, "_chromium_installed", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_version_mismatch_and_missing_package(self):
        def version(name):
            if name == "PyMuPDF":
                return "2.0"
            raise metadata.PackageNotFoundError(name)

        with mock.patch.object(envcheck.metadata, "version", side_effect=version):
            found = envcheck.problems()
        self.assertEqual(found, ["PyMuPDF: 2.0, нужен 1.26.7", "missing-lib: не установлен (нужен 1.0)"])

    def test_matching_environment(self):
        with mock.patch.object(envcheck.metadata, "version", side_effect=lambda name: {
                "PyMuPDF": "1.26.7", "missing-lib": "1.0"}[name]):
            self.assertEqual(envcheck.problems(), [])


if __name__ == "__main__":
    unittest.main()
