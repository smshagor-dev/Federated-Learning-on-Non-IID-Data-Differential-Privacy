#!/usr/bin/env python3
"""Fast validation entry point for the live FL simulator."""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.live_fl_simulator import SimulationConfig, run_simulation  # noqa: E402


def main() -> int:
    summary = run_simulation(
        SimulationConfig(
            clients=3,
            rounds=1,
            samples_per_client=16,
            features=4,
            classes=2,
            sample_rate=1.0,
            partition="iid",
            local_epochs=1,
            batch_size=16,
            seed=42,
            device="cpu",
            delay=0.0,
            clear_screen=False,
        ),
        sleep_fn=lambda _seconds: None,
    )
    if summary["server_rounds"] != 1:
        raise RuntimeError("expected exactly one completed server round")
    if not 0.0 <= summary["final_accuracy"] <= 1.0:
        raise RuntimeError("final accuracy is outside [0, 1]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
