#!/usr/bin/env python3
"""Calibrate client-level DP noise from a target epsilon.

Example:
    python scripts/calibrate_client_level_dp.py \
        --target-epsilon 4 --sample-rate 0.2 --rounds 50 --delta 1e-5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from federated.privacy_budget import calibrate_noise_multiplier  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate the Gaussian noise multiplier for a target client-level epsilon."
    )
    parser.add_argument("--target-epsilon", type=float, required=True)
    parser.add_argument("--sample-rate", type=float, required=True)
    parser.add_argument("--rounds", type=int, required=True)
    parser.add_argument("--delta", type=float, default=1e-5)
    parser.add_argument("--epsilon-tolerance", type=float, default=1e-4)
    parser.add_argument("--max-noise-multiplier", type=float, default=1e3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = calibrate_noise_multiplier(
        target_epsilon=args.target_epsilon,
        sample_rate=args.sample_rate,
        steps=args.rounds,
        delta=args.delta,
        epsilon_tolerance=args.epsilon_tolerance,
        max_noise_multiplier=args.max_noise_multiplier,
    )
    print(
        json.dumps(
            {
                "target_epsilon": result.target_epsilon,
                "achieved_epsilon": result.achieved_epsilon,
                "noise_multiplier": result.noise_multiplier,
                "sample_rate": result.sample_rate,
                "rounds": result.steps,
                "delta": result.delta,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
