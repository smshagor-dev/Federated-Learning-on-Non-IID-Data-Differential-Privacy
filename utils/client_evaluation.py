"""Post-run held-out client evaluation for the root federated runtime."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Mapping

import numpy as np
import torch

from data.partitioner import (
    get_dataset,
    partition_evaluation_by_train_distribution,
)
from models.networks import build_model
from utils.metrics import (
    client_evaluation_dict,
    client_evaluation_summary_dict,
    evaluate_client_partitions,
)
from utils.partition_metrics import write_partition_artifacts


def _load_training_partition(path: str) -> dict[int, np.ndarray]:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"training partition indices not found: {path}")
    result: dict[int, np.ndarray] = {}
    with np.load(path, allow_pickle=False) as archive:
        for key in archive.files:
            if not key.startswith("client_"):
                raise ValueError(f"unexpected partition entry {key!r}")
            client_id = int(key.split("_", 1)[1])
            result[client_id] = np.asarray(archive[key], dtype=np.int64)
    if not result:
        raise ValueError("training partition archive contains no clients")
    return dict(sorted(result.items()))


def _load_checkpoint(path: str) -> Mapping[str, torch.Tensor]:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"global model checkpoint not found: {path}")
    try:
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    except TypeError:
        checkpoint = torch.load(path, map_location="cpu")
    if not isinstance(checkpoint, dict) or "state_dict" not in checkpoint:
        raise ValueError(f"invalid global model checkpoint: {path}")
    state_dict = checkpoint["state_dict"]
    if not isinstance(state_dict, dict):
        raise ValueError(f"checkpoint state_dict is invalid: {path}")
    return state_dict


def _write_client_csv(path: str, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError("at least one client evaluation row is required")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _append_client_table(summary_path: str, algorithm_rows: list[dict[str, object]]) -> None:
    if not algorithm_rows:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n\n## Held-out Client Evaluation\n\n")
        handle.write(
            "| Algorithm | Mean Client Acc | P10 Client Acc | Worst Client Acc | "
            "Client Acc Std | Jain Index |\n"
        )
        handle.write("|---|---|---|---|---|---|\n")
        for row in algorithm_rows:
            handle.write(
                f"| {str(row['algorithm']).upper()} "
                f"| {float(row['mean_client_accuracy']) * 100:.2f}% "
                f"| {float(row['p10_client_accuracy']) * 100:.2f}% "
                f"| {float(row['worst_client_accuracy']) * 100:.2f}% "
                f"| {float(row['client_accuracy_std']):.4f} "
                f"| {float(row['jain_accuracy_index']):.4f} |\n"
            )


def evaluate_completed_run(config: dict) -> dict[str, object]:
    """Evaluate final global checkpoints on matched held-out client partitions."""
    results_dir = os.path.abspath(str(config["system"]["results_dir"]))
    summary_json_path = os.path.join(results_dir, "summary.json")
    summary_md_path = os.path.join(results_dir, "summary.md")
    if not os.path.isfile(summary_json_path):
        raise FileNotFoundError(f"machine summary not found: {summary_json_path}")

    with open(summary_json_path, "r", encoding="utf-8") as handle:
        machine_summary = json.load(handle)

    data_cfg = config["data"]
    train_set, test_set, num_classes, in_channels = get_dataset(
        str(data_cfg["dataset"]), str(data_cfg["data_root"])
    )
    train_partition = _load_training_partition(
        os.path.join(results_dir, "partition", "partition_indices.npz")
    )
    evaluation_partition = partition_evaluation_by_train_distribution(
        train_set,
        train_partition,
        test_set,
        seed=int(config["system"]["seed"]) + 1_000_003,
    )

    evaluation_partition_dir = os.path.join(results_dir, "evaluation_partition")
    manifest_path, indices_path, evaluation_manifest = write_partition_artifacts(
        client_dict=evaluation_partition,
        dataset=test_set,
        dataset_name=str(data_cfg["dataset"]),
        strategy="matched_heldout",
        seed=int(config["system"]["seed"]) + 1_000_003,
        parameters={
            "source_training_partition_hash": machine_summary["partition"][
                "partition_hash"
            ]
        },
        output_dir=evaluation_partition_dir,
    )

    device_name = str(config["system"].get("device", "auto"))
    if device_name == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(device_name)

    evaluation_rows: list[dict[str, object]] = []
    runs = machine_summary.get("runs") or []
    if not runs:
        raise ValueError("machine summary contains no algorithm runs")

    for run in runs:
        algorithm = str(run["algorithm"])
        checkpoint_path = os.path.join(
            results_dir, "checkpoints", f"global_model_{algorithm}.pt"
        )
        model = build_model(
            str(config["model"]["name"]),
            num_classes=num_classes,
            in_channels=in_channels,
            group_norm_groups=int(config["model"]["group_norm_groups"]),
        )
        model.load_state_dict(_load_checkpoint(checkpoint_path), strict=True)
        client_rows, client_summary = evaluate_client_partitions(
            model,
            test_set,
            evaluation_partition,
            batch_size=int(config["evaluation"]["eval_batch_size"]),
            device=device,
        )
        row_dicts = [client_evaluation_dict(row) for row in client_rows]
        client_csv = os.path.join(results_dir, f"client_evaluation_{algorithm}.csv")
        _write_client_csv(client_csv, row_dicts)
        summary_dict = client_evaluation_summary_dict(client_summary)

        final_accuracy = float(run["final_acc"])
        if abs(float(summary_dict["weighted_client_accuracy"]) - final_accuracy) > 1e-12:
            raise RuntimeError(
                "held-out client partition does not reproduce global test accuracy; "
                f"algorithm={algorithm}, global={final_accuracy}, "
                f"weighted_client={summary_dict['weighted_client_accuracy']}"
            )

        run["client_evaluation"] = {
            **summary_dict,
            "client_metrics_csv": client_csv,
            "checkpoint": checkpoint_path,
        }
        evaluation_rows.append({"algorithm": algorithm, **summary_dict})

    machine_summary["schema_version"] = max(
        2, int(machine_summary.get("schema_version", 1))
    )
    machine_summary["evaluation_partition"] = {
        **evaluation_manifest,
        "manifest_path": manifest_path,
        "indices_path": indices_path,
    }
    with open(summary_json_path, "w", encoding="utf-8") as handle:
        json.dump(machine_summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    if os.path.isfile(summary_md_path):
        _append_client_table(summary_md_path, evaluation_rows)

    return {
        "summary_json": summary_json_path,
        "evaluation_partition_manifest": manifest_path,
        "evaluation_partition_indices": indices_path,
        "algorithms": evaluation_rows,
    }
