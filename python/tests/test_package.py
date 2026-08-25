import unittest
from importlib.metadata import version

from fl_platform import __version__


class PackageTests(unittest.TestCase):
    def test_version_matches_installed_package_metadata(self) -> None:
        self.assertEqual(__version__, version("fl-platform"))
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+")
