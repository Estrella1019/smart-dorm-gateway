from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from shared.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_example_loads(self):
        path = Path(__file__).resolve().parents[1] / "config.example.toml"
        self.assertEqual(load_settings(str(path)).tcp_port, 9000)

    def test_invalid_values_rejected(self):
        for body in ('tcp_port = true', 'command_timeout = nan', 'max_connections = 0', 'typo = 2'):
            with self.subTest(body=body), TemporaryDirectory() as tmp:
                path = Path(tmp) / "bad.toml"
                path.write_text('[gateway]\n' + body, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_settings(str(path))
