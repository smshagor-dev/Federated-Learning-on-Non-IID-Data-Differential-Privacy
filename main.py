"""Canonical root entry point for the single-machine research simulator.

The distributed Go/C++/Python platform is a separate runtime documented in
RUNTIME.md and launched through Docker Compose.
"""

from __future__ import annotations

import os
import sys

from experiment_runtime import (
    apply_overrides,
    load_config,
    parse_args,
    run_cli,
    should_launch_gui,
    validate_config,
)


def _enforce_research_privacy_boundaries(config: dict) -> None:
    """Fail before execution when a privacy claim is not established."""
    algorithm = str(config["algorithm"]["name"]).lower()
    dp_enabled = bool(config["dp"]["enabled"])
    if dp_enabled and algorithm in {"scaffold", "all"}:
        raise ValueError(
            "DP-enabled SCAFFOLD is disabled in the active root runtime until "
            "the privacy effect of SCAFFOLD control-variate state/releases is "
            "formally established. Use FedAvg/FedProx with client-level DP, "
            "or run SCAFFOLD with DP disabled."
        )


def main(argv: list[str] | None = None) -> int:
    effective_argv = [] if argv is None else argv
    args = parse_args(effective_argv)
    config, warnings = validate_config(apply_overrides(load_config(args.config), args))
    _enforce_research_privacy_boundaries(config)
    launch_gui = should_launch_gui(args, effective_argv) if argv is not None else False
    if launch_gui:
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
        for warning in warnings:
            print(f"WARNING: {warning}")
        return int(launch_desktop_app(os.path.abspath(os.path.dirname(__file__)), args.config))

    for warning in warnings:
        print(f"WARNING: {warning}")
    run_cli(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
