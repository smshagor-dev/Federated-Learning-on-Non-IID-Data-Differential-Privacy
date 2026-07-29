"""Desktop-first entry point for the federated learning research studio."""

from __future__ import annotations

import os
import sys

from experiment_runtime import apply_overrides, load_config, parse_args, run_cli, should_launch_gui


def main() -> None:
    argv = sys.argv[1:]
    args = parse_args(argv)
    if should_launch_gui(args, argv):
        try:
            from desktop.app import launch_desktop_app
        except ModuleNotFoundError as exc:
            if exc.name == "PySide6":
                print(
                    "PySide6 is not installed. Run `pip install -r requirements.txt` "
                    "to install the desktop dashboard dependencies.",
                    file=sys.stderr,
                )
                raise SystemExit(1) from exc
            raise

        raise SystemExit(launch_desktop_app(os.path.abspath(os.path.dirname(__file__)), args.config))

    config = apply_overrides(load_config(args.config), args)
    run_cli(config)


if __name__ == "__main__":
    main()
