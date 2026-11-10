"""Execute a real torchvision PFL cell with auditable client-level holdout.

This is a non-private research protocol, separate from the production
distributed worker and its synthetic integration datasets. Each cell is
an independent dataset / partition / method / seed combination.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import platform
import random
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import numpy as np
import torch
from torch.utils.data import Dataset

from tai_pfl.engine import (
    METHODS, PERSONALIZED, State, TrainSpec, adapt_from_training,
    average_states, evaluate_client, load_snapshot, selected_clients,
    snapshot, summarize_client_rows, train_per_fedavg, train_regular,
)


def _commit_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _digest(data: object) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def _partition_digest(partitions: dict[int, np.ndarray]) -> str:
    values = {str(cid): np.asarray(indices, dtype=np.int64).tolist()
              for cid, indices in sorted(partitions.items())}
    return _digest(values)


def _require_complete_partition(partitions: dict[int, np.ndarray],
                                dataset_length: int, name: str) -> None:
    if sorted(partitions) != list(range(len(partitions))):
        raise ValueError(f"{name} client ids must be contiguous")
    arrays = [np.asarray(partitions[cid], dtype=np.int64)
              for cid in sorted(partitions)]
    if any(not len(values) for values in arrays):
        raise ValueError(f"{name} contains an empty client")
    all_indices = np.concatenate(arrays)
    if len(all_indices) != dataset_length or not np.array_equal(
        np.sort(all_indices), np.arange(dataset_length)
    ):
        raise ValueError(f"{name} must cover the split exactly once")


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("cannot write an empty scientific output")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _persist_indices(path: Path, partitions: dict[int, np.ndarray]) -> None:
    np.savez_compressed(path, **{f"client_{k}": np.asarray(v, dtype=np.int64)
                               for k, v in sorted(partitions.items())})


def run_cell(
    *, train_dataset: Dataset, test_dataset: Dataset,
    train_partitions: dict[int, np.ndarray], test_partitions: dict[int, np.ndarray],
    model_factory: Callable[[], torch.nn.Module], method: str, spec: TrainSpec,
    rounds: int, seed: int, output_dir: Path, dataset_id: str, partition_id: str,
    source_sha: str, official_torchvision: bool,
) -> dict:
    """Run exactly one cell. No synthetic result can pass the official-evidence flag."""
    spec.validate()
    if method not in METHODS:
        raise ValueError(f"unsupported algorithm: {method}")
    if rounds <= 0 or not source_sha:
        raise ValueError("positive rounds and attributable source commit required")
    if sorted(train_partitions) != sorted(test_partitions):
        raise ValueError("train/test client identities differ")
    _require_complete_partition(train_partitions, len(train_dataset), "training")
    _require_complete_partition(test_partitions, len(test_dataset), "held-out test")

    train_hash, test_hash = (
        _partition_digest(train_partitions), _partition_digest(test_partitions)
    )
    payload = {
        "dataset": dataset_id, "partition": partition_id, "method": method,
        "seed": int(seed), "rounds": int(rounds), "training": asdict(spec),
        "train_partition_hash": train_hash, "test_partition_hash": test_hash,
        "source_commit_sha": source_sha, "official_torchvision": official_torchvision,
        "dp_enabled": False,
    }
    output_dir = Path(output_dir)
    run_id = _digest(payload)
    if output_dir.exists() and any(output_dir.iterdir()):
        candidate = output_dir / "result.json"
        if candidate.exists():
            previous = json.loads(candidate.read_text(encoding="utf-8"))
            if previous.get("run_id") == run_id and previous.get("status") == "completed":
                return previous
        raise FileExistsError(
            f"non-empty output cannot be silently overwritten or reused: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "request.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _persist_indices(output_dir / "train_indices.npz", train_partitions)
    _persist_indices(output_dir / "test_indices.npz", test_partitions)

    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    prototype = model_factory()
    global_state = snapshot(prototype)
    participants = len(train_partitions)
    personal_states: dict[int, State] = {}
    participation_counts = {cid: 0 for cid in train_partitions}
    step_counts = {cid: 0 for cid in train_partitions}
    control_global: State = {
        name: torch.zeros_like(p.detach().cpu())
        for name, p in prototype.named_parameters()
    }
    control_local: dict[int, State] = {
        cid: {key: v.clone() for key, v in control_global.items()}
        for cid in train_partitions
    }
    round_rows: list[dict] = []
    for round_number in range(1, rounds + 1):
        selected = selected_clients(participants, spec.participation, seed, round_number)
        local_states: list[State] = []
        control_deltas: list[State] = []
        for cid in selected:
            torch.manual_seed(seed + round_number * 65537 + cid)
            local = copy.deepcopy(prototype)
            load_snapshot(local, global_state)
            batch_seed = seed + round_number * 100003 + cid * 103
            if method == "per_fedavg":
                local_state, steps = train_per_fedavg(
                    local, train_dataset, train_partitions[cid], spec, device, batch_seed
                )
            else:
                regular_method = "fedavg" if method == "ditto" else method
                local_state, steps, refreshed = train_regular(
                    local, train_dataset, train_partitions[cid], regular_method,
                    spec, device, batch_seed, global_state,
                    c_global=control_global if method == "scaffold" else None,
                    c_local=control_local[cid] if method == "scaffold" else None,
                )
                if refreshed is not None:
                    old_controls = control_local[cid]
                    control_deltas.append({
                        name: refreshed[name] - old_controls[name]
                        for name in old_controls
                    })
                    control_local[cid] = refreshed
            local_states.append(local_state)
            participation_counts[cid] += 1
            step_counts[cid] += steps
            if method == "ditto":
                personal = copy.deepcopy(prototype)
                load_snapshot(personal, personal_states.get(cid, global_state))
                personal_state, personal_steps, _ = train_regular(
                    personal, train_dataset, train_partitions[cid], "ditto",
                    spec, device, batch_seed + 531, global_state,
                )
                personal_states[cid] = personal_state
                step_counts[cid] += personal_steps
        global_state = average_states(global_state, local_states)
        if method == "scaffold":
            for name in control_global:
                control_global[name] = control_global[name] + sum(
                    (delta[name] for delta in control_deltas),
                    torch.zeros_like(control_global[name]),
                ) / participants
        round_rows.append({
            "round": round_number, "selected_client_count": len(selected),
            "total_optimizer_steps": sum(step_counts.values()),
        })

    _write_csv(output_dir / "rounds.csv", round_rows)
    torch.save(global_state, output_dir / "global_model.pt")
    if method == "ditto":
        torch.save(personal_states, output_dir / "ditto_client_models.pt")

    deployed: list[dict] = []
    global_rows: list[dict] = []
    min_test_n = min(len(v) for v in test_partitions.values())
    for cid in sorted(train_partitions):
        shared = copy.deepcopy(prototype)
        load_snapshot(shared, global_state)
        g = evaluate_client(shared, test_dataset, test_partitions[cid], device, spec.batch_size)
        global_rows.append({"client_id": cid, "n_train": len(train_partitions[cid]),
                            "participation_count": participation_counts[cid],
                            "optimizer_steps": step_counts[cid], **g})
        if method == "ditto":
            deployed_model = copy.deepcopy(prototype)
            load_snapshot(deployed_model, personal_states.get(cid, global_state))
        elif method == "per_fedavg":
            deployed_model = copy.deepcopy(prototype)
            load_snapshot(deployed_model, global_state)
            torch.manual_seed(seed + 10000019 + cid)
            personal_states[cid] = adapt_from_training(
                deployed_model, train_dataset, train_partitions[cid],
                spec, device, seed + 10000019 + cid,
            )
        else:
            deployed_model = shared
        p = evaluate_client(deployed_model, test_dataset, test_partitions[cid],
                            device, spec.batch_size)
        deployed.append({"client_id": cid, "n_train": len(train_partitions[cid]),
                         "participation_count": participation_counts[cid],
                         "optimizer_steps": step_counts[cid], **p})
    if method == "per_fedavg":
        torch.save(personal_states, output_dir / "per_fedavg_adapted_models.pt")
    _write_csv(output_dir / "clients_deployed.csv", deployed)
    _write_csv(output_dir / "clients_global.csv", global_rows)
    primary = summarize_client_rows(deployed)
    global_metrics = summarize_client_rows(global_rows)
    if sum(x["n_test"] for x in deployed) != len(test_dataset):
        raise RuntimeError("test split not fully covered at evaluation")
    result = {
        "schema_version": 1,
        "status": "completed",
        "run_id": run_id,
        "evidence_class": (
            "official_torchvision_heldout" if official_torchvision
            else "synthetic_unit_test_not_publishable"
        ),
        "request": payload,
        "primary_evaluation": "personalized_deployed" if method in PERSONALIZED else "shared_global",
        "metrics": primary,
        "global_reference_metrics": global_metrics,
        "minimum_heldout_client_samples": min_test_n,
        "training_uses_test_split": False,
        "personalized_models_aggregated": False,
        "model_architecture": type(prototype).__name__,
        "environment": {
            "python": platform.python_version(), "torch": torch.__version__,
            "numpy": np.__version__, "device": str(device),
        },
        "artifact_names": [
            "request.json", "train_indices.npz", "test_indices.npz",
            "rounds.csv", "clients_deployed.csv", "clients_global.csv",
            "global_model.pt",
        ],
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def partitions_for_condition(dataset: Dataset, *, condition: str, seed: int,
                             clients: int, minimum: int) -> dict[int, np.ndarray]:
    from data.partitioner import (
        partition_dirichlet, partition_iid, partition_pathological,
        partition_quantity_skew,
    )

    if condition == "iid":
        return partition_iid(dataset, clients, seed=seed, min_partition_size=minimum)
    if condition.startswith("dirichlet:"):
        return partition_dirichlet(
            dataset, clients, alpha=float(condition.split(":")[1]),
            seed=seed, min_partition_size=minimum,
        )
    if condition.startswith("pathological:"):
        return partition_pathological(
            dataset, clients, classes_per_client=int(condition.split(":")[1]),
            seed=seed, min_partition_size=minimum,
        )
    if condition.startswith("quantity_skew:"):
        return partition_quantity_skew(
            dataset, clients, quantity_skew_sigma=float(condition.split(":")[1]),
            seed=seed, min_partition_size=minimum,
        )
    raise ValueError(f"unsupported heterogeneity condition: {condition}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Official real-data PFL study; no synthetic fallbacks.")
    parser.add_argument("--dataset", choices=["MNIST", "FASHIONMNIST", "CIFAR10", "CIFAR100"], required=True)
    parser.add_argument("--condition", required=True)
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--rounds", type=int, default=50)
    parser.add_argument("--clients", type=int, default=20)
    parser.add_argument("--minimum-train", type=int, default=10)
    parser.add_argument("--output-dir", default="benchmarks/tai-pfl")
    parser.add_argument("--data-root", default="./data_raw")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--local-epochs", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--participation", type=float, default=0.2)
    parser.add_argument("--adaptation-steps", type=int, default=1)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    methods = tuple(x.strip() for x in args.methods.split(","))
    if not methods or len(set(methods)) != len(methods) or any(x not in METHODS for x in methods):
        parser.error("methods must be distinct and among " + ", ".join(METHODS))
    spec = TrainSpec(batch_size=args.batch_size, local_epochs=args.local_epochs,
                     learning_rate=args.learning_rate, participation=args.participation,
                     adaptation_steps=args.adaptation_steps)
    spec.validate()
    if args.clients < 2 or args.rounds < 1:
        parser.error("at least two clients and positive rounds are required")
    if args.plan_only:
        print(json.dumps({"dataset": args.dataset, "condition": args.condition,
                          "seed": args.seed, "rounds": args.rounds,
                          "methods": methods, "training": asdict(spec),
                          "executed": False}, indent=2))
        return 0
    revision = _commit_sha()
    if not revision:
        raise RuntimeError("a checked-out Git commit is required for attributable results")
    from data.partitioner import get_dataset, partition_evaluation_by_train_distribution
    from models.networks import build_model

    train, test, classes, channels = get_dataset(args.dataset, args.data_root)
    train_p = partitions_for_condition(
        train, condition=args.condition, seed=args.seed,
        clients=args.clients, minimum=args.minimum_train,
    )
    test_p = partition_evaluation_by_train_distribution(
        train, train_p, test, seed=args.seed + 1_000_003
    )
    _require_complete_partition(test_p, len(test), "official held-out test")
    factory = lambda: build_model(
        "cnn", num_classes=classes, in_channels=channels, group_norm_groups=2
    )
    for method in methods:
        folder = Path(args.output_dir) / args.dataset.lower() / args.condition.replace(":", "-") / str(args.seed) / method
        result = run_cell(
            train_dataset=train, test_dataset=test, train_partitions=train_p,
            test_partitions=test_p, model_factory=factory, method=method,
            spec=spec, rounds=args.rounds, seed=args.seed, output_dir=folder,
            dataset_id=args.dataset, partition_id=args.condition,
            source_sha=revision, official_torchvision=True,
        )
        print(f"{method}: {result['metrics']} -> {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
