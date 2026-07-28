from __future__ import annotations

import argparse

from .configuration import DEFAULT_PROFILE, PROFILE_TO_FILES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run and manage the Federated Learning Research Platform. "
            "Running `python main.py` with no subcommand starts the "
            "complete backend and local web platform."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_start_options(parser)
    subparsers = parser.add_subparsers(dest="command")

    start_parser = subparsers.add_parser(
        "start", help="Start the complete backend and local web platform."
    )
    add_start_options(start_parser)

    stop_parser = subparsers.add_parser(
        "stop", help="Stop the managed web process and backend containers."
    )
    stop_parser.add_argument(
        "--profile", choices=sorted(PROFILE_TO_FILES), default=None
    )

    restart_parser = subparsers.add_parser(
        "restart", help="Restart the complete backend and local web platform."
    )
    add_start_options(restart_parser)

    status_parser = subparsers.add_parser(
        "status", help="Show backend and web runtime status."
    )
    status_parser.add_argument("--json", action="store_true", dest="json_output")
    status_parser.add_argument(
        "--profile", choices=sorted(PROFILE_TO_FILES), default=None
    )

    health_parser = subparsers.add_parser(
        "health", help="Run active health checks for backend and web."
    )
    health_parser.add_argument("--json", action="store_true", dest="json_output")
    health_parser.add_argument(
        "--profile", choices=sorted(PROFILE_TO_FILES), default=None
    )

    doctor_parser = subparsers.add_parser(
        "doctor", help="Validate local dependencies and compose configuration."
    )
    doctor_parser.add_argument("--json", action="store_true", dest="json_output")
    doctor_parser.add_argument(
        "--profile", choices=sorted(PROFILE_TO_FILES), default=DEFAULT_PROFILE
    )
    doctor_parser.add_argument("--verbose", action="store_true")

    logs_parser = subparsers.add_parser(
        "logs", help="Show backend docker logs or managed web logs."
    )
    logs_parser.add_argument("service", nargs="?", default=None)
    logs_parser.add_argument("--follow", action="store_true")
    logs_parser.add_argument(
        "--profile", choices=sorted(PROFILE_TO_FILES), default=None
    )

    build_parser_cmd = subparsers.add_parser(
        "build", help="Build backend docker images."
    )
    build_parser_cmd.add_argument("services", nargs="*", default=[])
    build_parser_cmd.add_argument(
        "--profile", choices=sorted(PROFILE_TO_FILES), default=DEFAULT_PROFILE
    )
    build_parser_cmd.add_argument("--no-cache", action="store_true")

    clean_parser = subparsers.add_parser(
        "clean", help="Clean stale launcher state and stop project containers."
    )
    clean_parser.add_argument(
        "--profile", choices=sorted(PROFILE_TO_FILES), default=DEFAULT_PROFILE
    )
    clean_parser.add_argument("--volumes", action="store_true")
    clean_parser.add_argument("--yes", action="store_true")

    return parser


def add_start_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--build", action="store_true", help="Build backend images before starting."
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Use no-cache backend builds. Implies --build.",
    )
    parser.add_argument(
        "--keep-backend",
        action="store_true",
        help="On Ctrl+C, stop the web process but leave backend containers running.",
    )
    parser.add_argument(
        "--install-web",
        action="store_true",
        help=(
            "Install locked web dependencies with npm ci before starting "
            "the web server."
        ),
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_TO_FILES),
        default=DEFAULT_PROFILE,
        help="Compose profile chain to use for the backend stack.",
    )
    parser.add_argument("--web-port", type=int, default=3000)
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--verbose", action="store_true")
