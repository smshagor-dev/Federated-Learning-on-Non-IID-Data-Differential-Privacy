#!/usr/bin/env python3
"""Re-evaluate Per-FedAvg at 3/5 training-only adaptation steps.

This is a genuine held-out sensitivity experiment that REUSES an already
verified 50-round primary Per-FedAvg training checkpoint. The adaptation
budget is evaluation-only: no model is retrained and no test image is fitted.
It produces independently checkable result.json files, not synthetic scores.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

# Allow direct execution from scripts/ as well as python -m scripts.<module>.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import torch

from data.partitioner import get_dataset
from models.networks import build_model
from tai_pfl.analysis import _check_cell
from tai_pfl.engine import (
    TrainSpec,
    adapt_from_training,
    evaluate_client,
    load_snapshot,
    summarize_client_rows,
)
from tai_pfl.study import _digest, _partition_digest, _write_csv


def _read_partition(path: Path) -> dict[int, np.ndarray]:
    with np.load(path, allow_pickle=False) as archive:
        return {
            int(key.removeprefix("client_")): archive[key].copy()
            for key in archive.files
        }


def _reevaluate(
    *,
    reference: dict,
    source: Path,
    train_dataset: torch.utils.data.Dataset,
    test_dataset: torch.utils.data.Dataset,
    train_parts: dict[int, np.ndarray],
    test_parts: dict[int, np.ndarray],
    global_state: dict[str, torch.Tensor],
    classes: int,
    channels: int,
    spec: TrainSpec,
    seed: int,
) -> tuple[list[dict], dict, float]:
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    start = time.perf_counter()
    deployed: list[dict] = []
    checkpoints: dict[int, dict[str, torch.Tensor]] = {}
    with (source / "clients_deployed.csv").open(newline="", encoding="utf-8") as file:
        reference_rows = {
            int(row["client_id"]): row for row in csv.DictReader(file)
        }
    if set(reference_rows) != set(train_parts):
        raise ValueError("primary Per-FedAvg client rows mismatch")

    for cid in sorted(train_parts):
        model = build_model(
            "cnn", num_classes=classes, in_channels=channels,
            group_norm_groups=2,
        )
        load_snapshot(model, global_state)
        torch.manual_seed(seed + 10000019 + cid)
        checkpoints[cid] = adapt_from_training(
            model, train_dataset, train_parts[cid], spec, device,
            seed + 10000019 + cid,
        )
        observed = evaluate_client(
            model, test_dataset, test_parts[cid], device, spec.batch_size
        )
        baseline = reference_rows[cid]
        if spec.adaptation_steps == 1:
            if (observed["n_correct"] != int(baseline["n_correct"])
                or observed["n_test"] != int(baseline["n_test"])):
                raise ValueError(
                    f"primary 1-step reproducibility check failed, client {cid}"
                )
        deployed.append({
            "client_id": cid,
            "n_train": int(baseline["n_train"]),
            "participation_count": int(baseline["participation_count"]),
            "optimizer_steps": int(baseline["optimizer_steps"]),
            **observed,
        })
    return deployed, checkpoints, time.perf_counter() - start


def run(primary: Path, output_root: Path, data_root: Path,
        steps: tuple[int, ...]) -> list[dict]:
    original = _check_cell(primary / "result.json")
    request = original["request"]
    if request["method"] != "per_fedavg" or request["rounds"] != 50:
        raise ValueError("RQ4 requires a verified 50-round Per-FedAvg primary cell")
    if request["training"]["adaptation_steps"] != 1:
        raise ValueError("RQ4 primary reference must use one adaptation step")
    if any(step not in (3, 5) for step in steps):
        raise ValueError("RQ4 prespecifies adaptation steps 3 and 5")
    if len(set(steps)) != len(steps):
        raise ValueError("duplicate RQ4 adaptation steps")
    train, test, classes, channels = get_dataset(
        request["dataset"], str(data_root)
    )
    train_parts = _read_partition(primary / "train_indices.npz")
    test_parts = _read_partition(primary / "test_indices.npz")
    if _partition_digest(train_parts) != request["train_partition_hash"]:
        raise ValueError("training partition hash changed")
    if _partition_digest(test_parts) != request["test_partition_hash"]:
        raise ValueError("test partition hash changed")
    if (sum(map(len, train_parts.values())) != len(train)
        or sum(map(len, test_parts.values())) != len(test)):
        raise ValueError("official dataset size or partition coverage changed")
    saved = torch.load(
        primary / "global_model.pt", map_location="cpu", weights_only=True
    )
    if not isinstance(saved, dict) or not saved:
        raise ValueError("missing actual global model parameters")
    spec = TrainSpec(**request["training"])
    seed = int(request["seed"])
    # Reproduce the primary one-step predictions BEFORE testing higher budgets.
    _reevaluate(
        reference=original, source=primary, train_dataset=train,
        test_dataset=test, train_parts=train_parts, test_parts=test_parts,
        global_state=saved, classes=classes, channels=channels,
        spec=spec, seed=seed,
    )
    source_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    outputs = []
    for step in steps:
        amended = replace(spec, adaptation_steps=step)
        request2 = dict(request)
        request2["training"] = asdict(amended)
        request2["evaluation_source_commit_sha"] = source_sha
        request2["derived_from_primary_run_id"] = original["run_id"]
        request2["training_reused_not_repeated"] = True
        run_id = _digest(request2)
        target = (
            output_root / f"step-{step}" / request["dataset"].lower()
            / request["partition"].replace(":", "-") / str(seed) / "per_fedavg"
        )
        if target.exists() and any(target.iterdir()):
            previous = _check_cell(target / "result.json")
            if previous["run_id"] != run_id:
                raise ValueError(f"existing RQ4 run differs: {target}")
            outputs.append(previous)
            continue
        target.mkdir(parents=True, exist_ok=True)
        rows, states, elapsed = _reevaluate(
            reference=original, source=primary, train_dataset=train,
            test_dataset=test, train_parts=train_parts, test_parts=test_parts,
            global_state=saved, classes=classes, channels=channels,
            spec=amended, seed=seed,
        )
        for filename in (
            "train_indices.npz", "test_indices.npz", "global_model.pt",
            "rounds.csv", "clients_global.csv", "realized_heterogeneity.json",
        ):
            shutil.copy2(primary / filename, target / filename)
        torch.save(states, target / "per_fedavg_adapted_models.pt")
        _write_csv(target / "clients_deployed.csv", rows)
        (target / "request.json").write_text(
            json.dumps(request2, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result = {
            **original,
            "run_id": run_id,
            "request": request2,
            "metrics": summarize_client_rows(rows),
            "training_reused_from_primary": True,
            "source_primary_run_id": original["run_id"],
            "compute_observation": {
                **original["compute_observation"],
                "evaluation_wall_seconds": elapsed,
                "adaptation_steps_from_training_only": step * len(train_parts),
                "training_reused_not_repeated": True,
            },
            "artifact_names": [
                "request.json", "train_indices.npz", "test_indices.npz",
                "global_model.pt", "rounds.csv", "clients_global.csv",
                "clients_deployed.csv", "per_fedavg_adapted_models.pt",
                "realized_heterogeneity.json",
            ],
        }
        (target / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _check_cell(target / "result.json")
        outputs.append(result)
        print(
            f"Verified RQ4 training-reuse adaptation={step} "
            f"dataset={request['dataset']} condition={request['partition']} "
            f"seed={seed} metrics={result['metrics']}", flush=True,
        )
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verified RQ4 evaluation-only 3/5-step sensitivity"
    )
    parser.add_argument("--primary-cell", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data_raw"))
    parser.add_argument("--steps", type=int, nargs="+", default=[3, 5])
    args = parser.parse_args()
    run(args.primary_cell, args.output_root, args.data_root, tuple(args.steps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
