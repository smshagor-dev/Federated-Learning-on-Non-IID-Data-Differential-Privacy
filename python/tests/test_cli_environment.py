from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fl_platform.cli.environment import write_web_env_file


class WebEnvironmentFileTests(unittest.TestCase):
    def test_write_web_env_file_creates_expected_env_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            web_dir = Path(temp_dir)

            write_web_env_file(web_dir, "http://127.0.0.1:18080")

            payload = (web_dir / ".env.local").read_text(encoding="utf-8")

        self.assertIn("FL_API_BASE_URL=http://127.0.0.1:18080", payload)
        self.assertIn(
            "NEXT_PUBLIC_FL_API_BASE_URL=http://127.0.0.1:18080",
            payload,
        )


if __name__ == "__main__":
    unittest.main()
