import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class RepositoryDocsValidationTests(unittest.TestCase):
    def test_readme_validation_script_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_repository_docs.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=result.stdout + result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
