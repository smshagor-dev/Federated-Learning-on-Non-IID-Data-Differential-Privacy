"""Command-line arguments for the root federated-learning runtime."""

from __future__ import annotations

import argparse

from data.partitioner import SUPPORTED_DATASETS
from experiment_runtime import SUPPORTED_PARTITIONS
from federated.server import SUPPORTED_ALGORITHMS


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Federated Learning on Non-IID Data with Differential Privacy",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", type=str, default="config.yaml")
    parser.add_argument(
        "--algo",
        type=str,
        default=None,
        choices=list(SUPPORTED_ALGORITHMS) + ["all"],
        help="Aggregation algorithm",
    )
    parser.add_argument(
        "--partition",
        type=str,
        default=None,
        choices=list(SUPPORTED_PARTITIONS),
        help="Client partition strategy",
    )
    parser.add_argument("--alpha", type=float, default=None)
    parser.add_argument("--classes-per-client", type=int, default=None)
    parser.add_argument("--quantity-skew-sigma", type=float, default=None)
    parser.add_argument("--dp", type=str, default=None, choices=["on", "off"])
    parser.add_argument("--noise", type=float, default=None)
    parser.add_argument("--rounds", type=int, default=None)
    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        choices=list(SUPPORTED_DATASETS),
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--results-dir", type=str, default=None)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--cli", action="store_true")
    return parser.parse_args(argv)
