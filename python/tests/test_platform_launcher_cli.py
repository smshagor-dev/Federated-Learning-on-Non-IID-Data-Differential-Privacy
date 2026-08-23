import os
from pathlib import Path
import unittest
from unittest.mock import patch

from experiment_runtime import parse_args as runtime_parse_args
from fl_platform.cli.application import (
    _find_available_port,
    apply_automatic_port_overrides,
    resolve_runtime_compose,
    run_startup_checks,
)
from fl_platform.cli.configuration import resolve_paths
from fl_platform.cli.output import Console
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

    def test_root_main_bootstraps_root_cli_runtime(self) -> None:
        with patch("main._run_with_client_evaluation") as delegated, patch(
            "main.validate_config", side_effect=lambda cfg: (cfg, [])
        ):
            exit_code = root_main.main()

        self.assertEqual(exit_code, 0)
        delegated.assert_called_once()

    def test_root_runtime_cli_flag_is_supported(self) -> None:
        args = runtime_parse_args(["--cli", "--rounds", "2"])
        self.assertTrue(args.cli)
        self.assertEqual(args.rounds, 2)

    def test_run_startup_checks_supports_web_only_mode(self) -> None:
        paths = resolve_paths(Path(__file__).resolve().parents[2] / "main.py")

        checks = run_startup_checks(paths, None)

        self.assertIn(
            (
                "backend-mode",
                True,
                "Docker unavailable; launcher will continue in web-only mode.",
            ),
            checks,
        )

    def test_resolve_runtime_compose_returns_none_when_docker_missing(self) -> None:
        paths = resolve_paths(Path(__file__).resolve().parents[2] / "main.py")
        console = Console()

        with patch(
            "fl_platform.cli.application.check_command",
            return_value=type("Result", (), {"ok": False, "message": "missing"})(),
        ):
            compose = resolve_runtime_compose(paths, console, "development")

        self.assertIsNone(compose)

    def test_apply_automatic_port_overrides_sets_replacement_port(self) -> None:
        console = Console()
        original = os.environ.pop("FL_POSTGRES_HOST_PORT", None)
        self.addCleanup(self._restore_env, "FL_POSTGRES_HOST_PORT", original)

        with patch(
            "fl_platform.cli.application.is_port_available",
            side_effect=lambda _host, port: port != 5432 and port == 5433,
        ):
            apply_automatic_port_overrides(console)

        self.assertEqual(os.environ["FL_POSTGRES_HOST_PORT"], "5433")

    def test_apply_automatic_port_overrides_keeps_existing_value(self) -> None:
        console = Console()
        original = os.environ.get("FL_POSTGRES_HOST_PORT")
        os.environ["FL_POSTGRES_HOST_PORT"] = "55432"
        self.addCleanup(self._restore_env, "FL_POSTGRES_HOST_PORT", original)

        with patch("fl_platform.cli.application.is_port_available") as availability:
            apply_automatic_port_overrides(console)

        self.assertEqual(os.environ["FL_POSTGRES_HOST_PORT"], "55432")
        checked_ports = [call.args[1] for call in availability.call_args_list]
        self.assertNotIn(5432, checked_ports)

    def test_find_available_port_returns_none_when_range_is_full(self) -> None:
        with patch(
            "fl_platform.cli.application.is_port_available",
            return_value=False,
        ):
            result = _find_available_port(5432, attempts=2)

        self.assertIsNone(result)

    @staticmethod
    def _restore_env(name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


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
