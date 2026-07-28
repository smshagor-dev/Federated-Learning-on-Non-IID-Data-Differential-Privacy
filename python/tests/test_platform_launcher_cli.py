import unittest
from unittest.mock import patch

from fl_platform.cli.application import main as cli_main
from fl_platform.cli.compose import build_service_inventory
from fl_platform.cli.configuration import ServiceCategory
from fl_platform.cli.parser import build_parser

import main as root_main


class PlatformLauncherParserTests(unittest.TestCase):
    def test_help_mentions_default_no_argument_start(self) -> None:
        parser = build_parser()
        help_text = parser.format_help()

        self.assertIn("python", help_text)
        self.assertIn("main.py", help_text)
        self.assertIn("no subcommand starts the complete backend", help_text)
        self.assertNotIn(" all ", help_text)

    def test_no_all_subcommand_exists(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["all"])

    def test_no_arguments_leave_command_empty_for_default_start(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])

        self.assertIsNone(args.command)
        self.assertEqual(args.profile, "development")
        self.assertEqual(args.web_port, 3000)

    def test_explicit_start_alias_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["start", "--keep-backend"])

        self.assertEqual(args.command, "start")
        self.assertTrue(args.keep_backend)

    def test_management_subcommands_parse(self) -> None:
        parser = build_parser()

        for subcommand in [
            "stop",
            "restart",
            "status",
            "health",
            "logs",
            "build",
            "doctor",
            "clean",
        ]:
            with self.subTest(subcommand=subcommand):
                args = parser.parse_args([subcommand])
                self.assertEqual(args.command, subcommand)


class PlatformLauncherDispatchTests(unittest.TestCase):
    def test_cli_main_dispatches_no_args_to_start(self) -> None:
        with patch(
            "fl_platform.cli.application.start_command", return_value=0
        ) as start:
            exit_code = cli_main([])

        self.assertEqual(exit_code, 0)
        start.assert_called_once()

    def test_root_main_bootstraps_and_delegates(self) -> None:
        with patch("fl_platform.cli.application.main", return_value=7) as delegated:
            exit_code = root_main.main()

        self.assertEqual(exit_code, 7)
        delegated.assert_called_once_with()


class PlatformComposeInventoryTests(unittest.TestCase):
    def test_web_service_is_marked_for_exclusion(self) -> None:
        inventory = build_service_inventory(
            {
                "services": {
                    "api": {"ports": [{"published": 8080}]},
                    "web": {"ports": [{"published": 3000}]},
                    "python-worker": {},
                }
            }
        )

        self.assertFalse(inventory["api"].web)
        self.assertTrue(inventory["api"].backend)
        self.assertEqual(inventory["api"].category, ServiceCategory.API)
        self.assertTrue(inventory["web"].web)
        self.assertFalse(inventory["web"].backend)
        self.assertEqual(inventory["web"].published_url, "http://127.0.0.1:3000")
        self.assertTrue(inventory["python-worker"].required)


if __name__ == "__main__":
    unittest.main()
