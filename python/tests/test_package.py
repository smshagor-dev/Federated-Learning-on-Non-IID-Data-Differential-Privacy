import unittest

from fl_platform import __version__


class PackageTests(unittest.TestCase):
    def test_version_present(self) -> None:
        self.assertEqual(__version__, "0.1.0")
