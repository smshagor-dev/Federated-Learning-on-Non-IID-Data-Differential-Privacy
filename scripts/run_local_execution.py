#!/usr/bin/env python3
"""Execute a canonical execution specification on the existing root backend."""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main as root_main  # noqa: E402

SUPPORTED_DATASETS = {"MNIST", "FASHIONMNIST", "CIFAR10", "CIFAR100"}
SUPPORTED_ALGORITHMS = {"fedavg", "fedprox", "scaffold"}
SUPPORTED_PARTITIONS = {"iid", "dirichlet", "pathological", "quantity_skew"}
SUPPORTED_SAMPLING = {"poisson", "fixed_without_replacement"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one canonical federated execution on the local root backend."
    )
    parser.add_argument("--spec", required=True, help="Path to canonical execution JSON.")
    return parser.parse_args()


def _required_mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _finite_float(value: object, field: str, *, positive: bool = False) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed) or (positive and parsed <= 0.0):
        qualifier = "positive and finite" if positive else "finite"
        raise ValueError(f"{field} must be {qualifier}")
    return parsed


def load_spec(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    spec = _required_mapping(payload, "spec")
    if int(spec.get("schema_version", 0)) != 1:
        raise ValueError("schema_version must be 1")
    if spec.get("backend") != "local":
        raise ValueError("run_local_execution.py only accepts backend='local'")
    for section in (
        "dataset",
        "model",
        "algorithm",
        "optimizer",
        "federation",
        "privacy",
        "evaluation",
        "artifacts",
        "security",
    ):
        _required_mapping(spec.get(section), section)
    return spec


def build_root_config(spec: dict[str, Any]) -> dict[str, Any]:
    base = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    if not isinstance(base, dict):
        raise ValueError("root config.yaml must contain an object")
    config = copy.deepcopy(base)

    dataset = _required_mapping(spec["dataset"], "dataset")
    partition = _required_mapping(dataset.get("partition"), "dataset.partition")
    dataset_name = str(dataset.get("name", "")).upper()
    if dataset_name not in SUPPORTED_DATASETS:
        raise ValueError(f"local backend dataset is unsupported: {dataset_name!r}")
    partition_strategy = str(partition.get("strategy", "")).lower()
    if partition_strategy not in SUPPORTED_PARTITIONS:
        raise ValueError(
            f"local backend partition is unsupported: {partition_strategy!r}"
        )

    model = _required_mapping(spec["model"], "model")
    architecture = str(model.get("architecture_name") or model.get("name") or "").lower()
    if architecture != "cnn":
        raise ValueError(
            "local root backend currently maps only model architecture_name='cnn'"
        )

    algorithm = _required_mapping(spec["algorithm"], "algorithm")
    algorithm_name = str(algorithm.get("name", "")).lower()
    if algorithm_name not in SUPPORTED_ALGORITHMS:
        raise ValueError(
            f"local root backend algorithm is unsupported: {algorithm_name!r}"
        )

    federation = _required_mapping(spec["federation"], "federation")
    total_clients = _positive_int(
        federation.get("total_clients"), "federation.total_clients"
    )
    target_clients = _positive_int(
        federation.get("target_clients_per_round"),
        "federation.target_clients_per_round",
    )
    if target_clients > total_clients:
        raise ValueError("target_clients_per_round cannot exceed total_clients")
    sampling = str(federation.get("sampling_strategy", "")).lower()
    if sampling not in SUPPORTED_SAMPLING:
        raise ValueError(f"local sampling strategy is unsupported: {sampling!r}")
    if federation.get("scheduling_mode") != "synchronous":
        raise ValueError(
            "local root backend currently supports synchronous scheduling only"
        )

    privacy = _required_mapping(spec["privacy"], "privacy")
    privacy_mode = str(privacy.get("mode", "none"))
    if privacy_mode not in {"none", "user_level_dp"}:
        raise ValueError(
            "local root backend supports privacy.mode='none' or 'user_level_dp' only"
        )
    user_level = _required_mapping(privacy.get("user_level", {}), "privacy.user_level")
    adaptive = _required_mapping(
        privacy.get("adaptive_clipping", {}), "privacy.adaptive_clipping"
    )
    if bool(adaptive.get("enabled", False)):
        raise ValueError("adaptive clipping is not implemented by the local root backend")

    security = _required_mapping(spec["security"], "security")
    if any(
        bool(security.get(key, False))
        for key in (
            "require_authenticated_workers",
            "require_signed_tasks",
            "require_signed_results",
            "secure_aggregation",
        )
    ):
        raise ValueError(
            "worker transport/signing/secure-aggregation policies require the "
            "distributed backend"
        )

    if privacy_mode == "user_level_dp":
        if algorithm_name == "scaffold":
            raise ValueError("local DP-enabled SCAFFOLD is intentionally unsupported")
        if sampling != "poisson":
            raise ValueError("local client-level DP requires poisson client sampling")
        if str(federation.get("weighting", "")) != "uniform":
            raise ValueError("local client-level DP requires uniform client weighting")
        if str(user_level.get("accountant", "")).lower() != "rdp":
            raise ValueError("local root backend currently uses the RDP accountant only")
        if str(user_level.get("weighting_strategy", "")).lower() != "uniform":
            raise ValueError(
                "local root backend user-level DP requires uniform weighting_strategy"
            )
        if bool(user_level.get("secure_random", False)):
            raise ValueError(
                "local root backend does not claim a cryptographically secure "
                "Gaussian RNG"
            )
        epsilon_budget = _finite_float(
            user_level.get("epsilon_budget", 0.0),
            "privacy.user_level.epsilon_budget",
        )
        if epsilon_budget < 0.0:
            raise ValueError("privacy.user_level.epsilon_budget must be non-negative")
        if epsilon_budget > 0.0:
            raise ValueError(
                "local root backend does not yet implement epsilon_budget stop-policy "
                "enforcement; target_epsilon calibration is intentionally not treated "
                "as the same contract"
            )

    optimizer = _required_mapping(spec["optimizer"], "optimizer")
    evaluation = _required_mapping(spec["evaluation"], "evaluation")
    artifacts = _required_mapping(spec["artifacts"], "artifacts")
    artifact_root = Path(str(artifacts.get("root", "")))
    if not str(artifact_root):
        raise ValueError("artifacts.root is required")
    if not artifact_root.is_absolute():
        artifact_root = (ROOT / artifact_root).resolve()

    config["system"]["seed"] = int(federation.get("client_selection_seed", 0))
    config["system"]["results_dir"] = str(artifact_root)
    config["data"]["dataset"] = dataset_name
    config["data"]["partition"] = partition_strategy
    config["data"]["alpha"] = _finite_float(
        partition.get("alpha", config["data"].get("alpha", 0.1)),
        "dataset.partition.alpha",
        positive=True,
    )
    config["data"]["classes_per_client"] = int(
        partition.get(
            "classes_per_client", config["data"].get("classes_per_client", 2)
        )
    )
    config["data"]["quantity_skew_sigma"] = _finite_float(
        partition.get(
            "quantity_skew_sigma", config["data"].get("quantity_skew_sigma", 1.0)
        ),
        "dataset.partition.quantity_skew_sigma",
    )
    config["data"]["min_partition_size"] = int(
        partition.get(
            "minimum_client_size", config["data"].get("min_partition_size", 10)
        )
    )

    config["federated"]["num_clients"] = total_clients
    config["federated"]["sample_rate"] = target_clients / float(total_clients)
    config["federated"]["sampling_strategy"] = sampling
    config["federated"]["aggregation_weighting"] = str(
        federation.get("weighting", "uniform")
    )
    config["federated"]["rounds"] = _positive_int(
        federation.get("rounds"), "federation.rounds"
    )
    config["federated"]["local_epochs"] = _positive_int(
        federation.get("local_epochs"), "federation.local_epochs"
    )
    config["federated"]["batch_size"] = _positive_int(
        federation.get("batch_size"), "federation.batch_size"
    )
    config["federated"]["server_lr"] = _finite_float(
        optimizer.get("server_lr"), "optimizer.server_lr", positive=True
    )

    config["optimizer"]["lr"] = _finite_float(
        optimizer.get("learning_rate"), "optimizer.learning_rate", positive=True
    )
    config["optimizer"]["momentum"] = _finite_float(
        optimizer.get("momentum", 0.0), "optimizer.momentum"
    )
    config["optimizer"]["weight_decay"] = _finite_float(
        optimizer.get("weight_decay", 0.0), "optimizer.weight_decay"
    )
    config["algorithm"]["name"] = algorithm_name
    config["algorithm"]["mu"] = _finite_float(
        algorithm.get("mu", 0.0), "algorithm.mu"
    )
    config["model"]["name"] = "cnn"

    config["dp"]["enabled"] = privacy_mode == "user_level_dp"
    config["dp"]["target_epsilon"] = None
    if privacy_mode == "user_level_dp":
        config["dp"]["update_clip_norm"] = _finite_float(
            user_level.get("initial_clipping_bound"),
            "privacy.user_level.initial_clipping_bound",
            positive=True,
        )
        config["dp"]["noise_multiplier"] = _finite_float(
            user_level.get("noise_multiplier"),
            "privacy.user_level.noise_multiplier",
            positive=True,
        )
        target_delta = _finite_float(
            user_level.get("target_delta"),
            "privacy.user_level.target_delta",
            positive=True,
        )
        if target_delta >= 1.0:
            raise ValueError("privacy.user_level.target_delta must lie in (0, 1)")
        config["dp"]["target_delta"] = target_delta

    config["evaluation"]["eval_batch_size"] = _positive_int(
        evaluation.get("evaluation_batch_size"),
        "evaluation.evaluation_batch_size",
    )
    return config


def execute(spec_path: Path) -> int:
    spec = load_spec(spec_path)
    config = build_root_config(spec)
    result_root = Path(config["system"]["results_dir"])
    result_root.mkdir(parents=True, exist_ok=True)
    generated_config = result_root / "_canonical_execution_config.json"
    generated_config.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return int(root_main.main(["--cli", "--config", str(generated_config)]))


def main() -> int:
    args = parse_args()
    return execute(Path(args.spec).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
