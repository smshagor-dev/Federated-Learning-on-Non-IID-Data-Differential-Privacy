"""Canonical entry point for the single-machine federated-learning runtime.

The distributed Go/C++/Python platform is a separate runtime documented in
RUNTIME.md and launched through Docker Compose.
"""

from __future__ import annotations

import os
import sys

import yaml

from experiment_runtime import (
    apply_overrides,
    load_config,
    run_cli,
    should_launch_gui,
    validate_config,
)
from federated.privacy_budget import resolve_target_epsilon_config
from federated.resumable_runtime import run_resumable_cli
from utils.client_evaluation import evaluate_completed_run
from utils.runtime_args import parse_args

_CHECKPOINT_DIR_ENV = "FL_ROOT_CHECKPOINT_DIR"
_CHECKPOINT_ROUNDS_ENV = "FL_ROOT_CHECKPOINT_ROUNDS"


def _enforce_privacy_boundaries(config: dict) -> None:
    """Fail before execution when a requested privacy guarantee is unsupported."""
    algorithm = str(config["algorithm"]["name"]).lower()
    dp_enabled = bool(config["dp"]["enabled"])
    if dp_enabled and algorithm in {"scaffold", "all"}:
        raise ValueError(
            "DP-enabled SCAFFOLD is disabled in the root runtime until the privacy "
            "effect of SCAFFOLD control-variate state/releases is formally covered. "
            "Use FedAvg/FedProx with client-level DP, or run SCAFFOLD with DP disabled."
        )


def _resolve_effective_privacy_config(
    config: dict,
    *,
    manual_noise_override: bool,
    warnings: list[str],
) -> dict:
    resolved, calibration = resolve_target_epsilon_config(
        config,
        manual_noise_override=manual_noise_override,
    )
    if calibration is not None:
        warnings.append(
            "Calibrated client-level DP noise from target epsilon after all runtime "
            f"overrides: target_epsilon={calibration.target_epsilon:.6g}, "
            f"achieved_epsilon={calibration.achieved_epsilon:.6g}, "
            f"sigma={calibration.noise_multiplier:.8g}, "
            f"q={calibration.sample_rate:.6g}, rounds={calibration.steps}, "
            f"delta={calibration.delta:.6g}."
        )
    elif manual_noise_override and bool(resolved["dp"].get("enabled", False)):
        warnings.append(
            "Explicit --noise override selected; target_epsilon is disabled in the "
            "effective runtime config and privacy is reported from the supplied sigma."
        )
    return resolved


def write_effective_runtime_config(config: dict) -> str:
    """Persist the exact post-override config used by the root CLI run."""
    results_dir = os.path.abspath(str(config["system"]["results_dir"]))
    os.makedirs(results_dir, exist_ok=True)
    target = os.path.join(results_dir, "_effective_runtime_config.yaml")
    with open(target, "w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)
    return target


def _run_selected_cli(config: dict) -> None:
    control = config.get("execution_control")
    if isinstance(control, dict) and bool(control.get("enabled", False)):
        run_resumable_cli(config)
        return
    run_cli(config)


def _run_with_client_evaluation(config: dict) -> None:
    results_dir = os.path.abspath(str(config["system"]["results_dir"]))
    checkpoint_dir = os.path.join(results_dir, "checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)
    previous_checkpoint_dir = os.environ.get(_CHECKPOINT_DIR_ENV)
    previous_checkpoint_rounds = os.environ.get(_CHECKPOINT_ROUNDS_ENV)
    os.environ[_CHECKPOINT_DIR_ENV] = checkpoint_dir
    os.environ[_CHECKPOINT_ROUNDS_ENV] = str(config["federated"]["rounds"])
    try:
        _run_selected_cli(config)
        evaluation = evaluate_completed_run(config)
        print(
            "Held-out client evaluation written -> "
            f"{evaluation['evaluation_partition_manifest']}"
        )
    finally:
        if previous_checkpoint_dir is None:
            os.environ.pop(_CHECKPOINT_DIR_ENV, None)
        else:
            os.environ[_CHECKPOINT_DIR_ENV] = previous_checkpoint_dir
        if previous_checkpoint_rounds is None:
            os.environ.pop(_CHECKPOINT_ROUNDS_ENV, None)
        else:
            os.environ[_CHECKPOINT_ROUNDS_ENV] = previous_checkpoint_rounds


def main(argv: list[str] | None = None) -> int:
    effective_argv = [] if argv is None else argv
    args = parse_args(effective_argv)
    config, warnings = validate_config(apply_overrides(load_config(args.config), args))
    config = _resolve_effective_privacy_config(
        config,
        manual_noise_override=args.noise is not None,
        warnings=warnings,
    )
    _enforce_privacy_boundaries(config)
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
        return int(
            launch_desktop_app(os.path.abspath(os.path.dirname(__file__)), args.config)
        )

    effective_config_path = write_effective_runtime_config(config)
    warnings.append(f"Effective runtime configuration archived at {effective_config_path}.")
    for warning in warnings:
        print(f"WARNING: {warning}")
    _run_with_client_evaluation(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
