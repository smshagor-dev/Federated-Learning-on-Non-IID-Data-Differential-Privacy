"""Resumable orchestration for canonical local executions.

This module reuses the active root runtime's client/server/partition/privacy and
reporting components, adding only round-boundary checkpoint and pause semantics.
"""

from __future__ import annotations

import csv
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.utils.data import DataLoader

import experiment_runtime as base
from federated.client import Client
from federated.dp_accountant import MomentsAccountant
from federated.runtime_checkpoint import (
    CheckpointError,
    restore_runtime_checkpoint,
    save_runtime_checkpoint,
)
from federated.server import Server
from models.networks import build_model
from utils.logger import CSVLogger, generate_all_plots


class ExecutionPaused(RuntimeError):
    """Raised only after a safe round-boundary checkpoint is durable."""

    def __init__(self, rounds_completed: int, checkpoint_path: str) -> None:
        super().__init__(
            f"execution paused after round {rounds_completed}; checkpoint={checkpoint_path}"
        )
        self.rounds_completed = int(rounds_completed)
        self.checkpoint_path = checkpoint_path


@dataclass(slots=True, frozen=True)
class ExecutionControl:
    checkpoint_enabled: bool
    resume: bool
    checkpoint_path: str
    pause_request_path: str
    paused_marker_path: str

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "ExecutionControl":
        section = config.get("execution_control")
        if not isinstance(section, dict) or not bool(section.get("enabled", False)):
            raise ValueError("execution_control.enabled must be true")
        control_dir = Path(str(section.get("control_dir", ""))).resolve()
        if not str(section.get("control_dir", "")).strip():
            raise ValueError("execution_control.control_dir is required")
        checkpoint_path = Path(
            str(section.get("checkpoint_path") or control_dir / "runtime-checkpoint.pt")
        ).resolve()
        pause_request_path = Path(
            str(section.get("pause_request_path") or control_dir / "pause.request")
        ).resolve()
        paused_marker_path = Path(
            str(section.get("paused_marker_path") or control_dir / "paused.json")
        ).resolve()
        for path in (checkpoint_path, pause_request_path, paused_marker_path):
            if path.parent != control_dir:
                raise ValueError(
                    "execution control files must live directly under control_dir"
                )
        control_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_enabled = bool(section.get("checkpoint_enabled", False))
        resume = bool(section.get("resume", False))
        if resume and not checkpoint_enabled:
            raise ValueError("resume requires execution_control.checkpoint_enabled=true")
        return cls(
            checkpoint_enabled=checkpoint_enabled,
            resume=resume,
            checkpoint_path=str(checkpoint_path),
            pause_request_path=str(pause_request_path),
            paused_marker_path=str(paused_marker_path),
        )


def _write_json_atomic(path: str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


def _validate_resume_csv(csv_path: str, algorithm: str, rounds_completed: int) -> None:
    path = Path(csv_path)
    if rounds_completed == 0:
        return
    if not path.is_file():
        raise CheckpointError(
            "runtime checkpoint has completed rounds but the round CSV is missing"
        )
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != rounds_completed:
        raise CheckpointError(
            "round CSV row count does not match runtime checkpoint rounds_completed"
        )
    for expected_round, row in enumerate(rows, start=1):
        if int(row.get("round", 0)) != expected_round:
            raise CheckpointError("round CSV sequence does not match checkpoint history")
        if str(row.get("algorithm", "")).lower() != algorithm.lower():
            raise CheckpointError("round CSV algorithm does not match checkpoint")


def _fresh_start_has_partial_history(csv_path: str) -> bool:
    path = Path(csv_path)
    if not path.is_file() or path.stat().st_size == 0:
        return False
    with path.open(newline="", encoding="utf-8") as handle:
        return any(True for _ in csv.DictReader(handle))


def _checkpoint_round(
    *,
    control: ExecutionControl,
    config: dict[str, Any],
    algorithm: str,
    round_id: int,
    history: list[dict[str, Any]],
    elapsed_sec: float,
    server: Server,
    sampler,
    privacy_generator: torch.Generator | None,
    accountant: MomentsAccountant | None,
) -> str | None:
    if not control.checkpoint_enabled:
        return None
    return save_runtime_checkpoint(
        control.checkpoint_path,
        config=config,
        algorithm=algorithm,
        rounds_completed=round_id,
        history=history,
        elapsed_sec=elapsed_sec,
        server=server,
        sampler=sampler,
        privacy_generator=privacy_generator,
        accountant=accountant,
    )


def run_resumable_experiment(
    *,
    algorithm: str,
    config: dict,
    train_set,
    test_loader: DataLoader,
    client_dict: dict[int, np.ndarray],
    num_classes: int,
    in_channels: int,
    device: torch.device,
    control: ExecutionControl,
) -> dict:
    base.set_seed(int(config["system"]["seed"]))
    fed_cfg = config["federated"]
    dp_cfg = config["dp"]
    num_clients = int(fed_cfg["num_clients"])
    rounds = int(fed_cfg["rounds"])
    sample_rate = float(fed_cfg["sample_rate"])
    sampling_strategy = str(fed_cfg["sampling_strategy"])
    aggregation_weighting = str(fed_cfg["aggregation_weighting"])
    results_dir = config["system"]["results_dir"]
    dp_enabled = bool(dp_cfg["enabled"])

    global_model = build_model(
        config["model"]["name"],
        num_classes=num_classes,
        in_channels=in_channels,
        group_norm_groups=int(config["model"]["group_norm_groups"]),
    )
    privacy_generator, privacy_status = base._make_privacy_noise_generator(dp_cfg)
    server = Server(
        model=global_model,
        num_clients=num_clients,
        algorithm=algorithm,
        server_lr=float(fed_cfg["server_lr"]),
        device=device,
        aggregation_weighting=aggregation_weighting,
        dp_enabled=dp_enabled,
        noise_multiplier=float(dp_cfg["noise_multiplier"]),
        update_clip_norm=float(dp_cfg["update_clip_norm"]),
        privacy_noise_generator=privacy_generator,
    )
    clients = [
        Client(client_id, train_set, client_dict[client_id], config, device)
        for client_id in range(num_clients)
    ]
    scratch_model = build_model(
        config["model"]["name"],
        num_classes=num_classes,
        in_channels=in_channels,
        group_norm_groups=int(config["model"]["group_norm_groups"]),
    )
    accountant: MomentsAccountant | None = None
    if dp_enabled:
        accountant = MomentsAccountant(
            noise_multiplier=float(dp_cfg["noise_multiplier"]),
            sample_rate=sample_rate,
            target_delta=float(dp_cfg["target_delta"]),
        )

    sampler = base.random.Random(int(config["system"]["seed"]))
    csv_path = os.path.join(results_dir, f"run_{algorithm}.csv")
    history: list[dict[str, Any]] = []
    elapsed_before = 0.0
    start_round = 1

    if control.resume:
        restored = restore_runtime_checkpoint(
            control.checkpoint_path,
            config=config,
            algorithm=algorithm,
            server=server,
            sampler=sampler,
            privacy_generator=privacy_generator,
            accountant=accountant,
        )
        history = restored.history
        elapsed_before = restored.elapsed_sec
        start_round = restored.rounds_completed + 1
        _validate_resume_csv(csv_path, algorithm, restored.rounds_completed)
    else:
        if control.checkpoint_enabled and Path(control.checkpoint_path).exists():
            raise CheckpointError(
                "fresh execution found an existing runtime checkpoint; use resume or a unique artifact root"
            )
        if _fresh_start_has_partial_history(csv_path):
            raise CheckpointError(
                "fresh execution found existing round history without an active resume request"
            )

    logger = CSVLogger(csv_path, append=control.resume)
    start = time.time()
    print(
        f"\n=== {algorithm.upper()} | {rounds} rounds | "
        f"sampling={sampling_strategy} q={sample_rate:.3f} | "
        f"weighting={aggregation_weighting} | DP={'on' if dp_enabled else 'off'} | "
        f"resume_from={start_round - 1} ==="
    )

    try:
        for round_id in range(start_round, rounds + 1):
            selected = base._sample_client_ids(
                num_clients=num_clients,
                sample_rate=sample_rate,
                strategy=sampling_strategy,
                sampler=sampler,
            )
            global_state = server.broadcast()

            client_results = []
            for client_id in selected:
                c_global, c_local = server.get_control_variates(client_id)
                result = clients[client_id].train(
                    model=scratch_model,
                    global_state=global_state,
                    algorithm=algorithm,
                    c_global=c_global,
                    c_local=c_local,
                )
                client_results.append(result)

            aggregate_stats = server.aggregate(client_results)

            epsilon_value = math.nan
            total_rdp: list[float] | None = None
            if accountant is not None:
                accountant.step()
                estimate = accountant.estimate()
                epsilon_value = estimate.epsilon
                total_rdp = estimate.total_rdp.tolist()

            test_loss, test_acc = base.evaluate_global(server.model, test_loader, device)
            weight_var = base.compute_weight_variance(
                [result["local_state"] for result in client_results]
            )
            raw_drift = base.compute_client_drift(
                [result["raw_delta"] for result in client_results]
            )
            clipped_drift = base.compute_client_drift(
                [result["clipped_delta"] for result in client_results]
            )
            mean_unclipped_update_norm = (
                float(
                    np.mean(
                        [
                            result["unclipped_update_norm"]
                            for result in client_results
                        ]
                    )
                )
                if client_results
                else 0.0
            )
            mean_clipping_factor = (
                float(np.mean([result["clipping_factor"] for result in client_results]))
                if client_results
                else 1.0
            )
            fraction_clients_clipped = (
                float(
                    np.mean(
                        [
                            1.0 if result["was_clipped"] else 0.0
                            for result in client_results
                        ]
                    )
                )
                if client_results
                else 0.0
            )
            avg_client_loss = (
                float(np.mean([result["avg_loss"] for result in client_results]))
                if client_results
                else 0.0
            )
            participation_rate = len(selected) / float(num_clients)

            row = {
                "round": round_id,
                "algorithm": algorithm,
                "cohort_size": len(selected),
                "participation_rate": f"{participation_rate:.6f}",
                "test_acc": f"{test_acc:.6f}",
                "test_loss": f"{test_loss:.6f}",
                "epsilon": ""
                if not dp_enabled
                else (
                    f"{epsilon_value:.6f}" if math.isfinite(epsilon_value) else "inf"
                ),
                "weight_variance": f"{weight_var:.8e}",
                "raw_client_drift": f"{raw_drift:.8e}",
                "clipped_client_drift": f"{clipped_drift:.8e}",
                "mean_unclipped_update_norm": f"{mean_unclipped_update_norm:.8e}",
                "mean_clipping_factor": f"{mean_clipping_factor:.8e}",
                "fraction_clients_clipped": f"{fraction_clients_clipped:.8e}",
                "aggregate_noise_norm": f"{aggregate_stats['aggregate_noise_norm']:.8e}",
                "avg_client_loss": f"{avg_client_loss:.6f}",
            }
            logger.log(row)
            history.append(
                {
                    "round": round_id,
                    "cohort_size": len(selected),
                    "participation_rate": participation_rate,
                    "test_acc": test_acc,
                    "test_loss": test_loss,
                    "epsilon": epsilon_value,
                    "weight_variance": weight_var,
                    "raw_client_drift": raw_drift,
                    "clipped_client_drift": clipped_drift,
                    "mean_unclipped_update_norm": mean_unclipped_update_norm,
                    "mean_clipping_factor": mean_clipping_factor,
                    "fraction_clients_clipped": fraction_clients_clipped,
                    "aggregate_noise_norm": aggregate_stats["aggregate_noise_norm"],
                    "rdp": total_rdp,
                }
            )

            epsilon_text = (
                "n/a"
                if not dp_enabled
                else (
                    f"{epsilon_value:6.2f}"
                    if math.isfinite(epsilon_value)
                    else "inf"
                )
            )
            print(
                f"[{algorithm:8s}] round {round_id:3d}/{rounds} | "
                f"cohort {len(selected):3d}/{num_clients} | "
                f"acc {test_acc * 100:5.2f}% | loss {test_loss:.4f} | "
                f"eps {epsilon_text} | raw drift {raw_drift:.3e}"
            )

            elapsed_now = elapsed_before + (time.time() - start)
            checkpoint_path = _checkpoint_round(
                control=control,
                config=config,
                algorithm=algorithm,
                round_id=round_id,
                history=history,
                elapsed_sec=elapsed_now,
                server=server,
                sampler=sampler,
                privacy_generator=privacy_generator,
                accountant=accountant,
            )
            if Path(control.pause_request_path).exists():
                if checkpoint_path is None:
                    raise RuntimeError(
                        "pause requested but checkpoint persistence is disabled"
                    )
                _write_json_atomic(
                    control.paused_marker_path,
                    {
                        "schema_version": 1,
                        "status": "PAUSED",
                        "algorithm": algorithm,
                        "rounds_completed": round_id,
                        "checkpoint_path": checkpoint_path,
                    },
                )
                raise ExecutionPaused(round_id, checkpoint_path)
    finally:
        logger.close()

    elapsed = elapsed_before + (time.time() - start)
    if not history:
        raise RuntimeError("experiment completed without any round history")
    accuracies = [item["test_acc"] for item in history]
    return {
        "algorithm": algorithm,
        "csv_path": csv_path,
        "final_acc": accuracies[-1],
        "best_acc": max(accuracies),
        "final_loss": history[-1]["test_loss"],
        "final_epsilon": history[-1]["epsilon"],
        "mean_raw_drift": float(
            np.mean([item["raw_client_drift"] for item in history])
        ),
        "mean_clipped_drift": float(
            np.mean([item["clipped_client_drift"] for item in history])
        ),
        "mean_weight_var": float(
            np.mean([item["weight_variance"] for item in history])
        ),
        "mean_update_norm": float(
            np.mean([item["mean_unclipped_update_norm"] for item in history])
        ),
        "mean_clipping_factor": float(
            np.mean([item["mean_clipping_factor"] for item in history])
        ),
        "mean_fraction_clipped": float(
            np.mean([item["fraction_clients_clipped"] for item in history])
        ),
        "mean_aggregate_noise_norm": float(
            np.mean([item["aggregate_noise_norm"] for item in history])
        ),
        "elapsed_sec": elapsed,
        "rounds_completed": len(history),
        "rdp_orders": accountant.orders if accountant is not None else [],
        "final_total_rdp": history[-1]["rdp"],
        "privacy_status": privacy_status,
    }


def run_resumable_cli(config: dict) -> str:
    config, warnings = base.validate_config(config)
    control = ExecutionControl.from_config(config)
    seed = int(config["system"]["seed"])
    base.set_seed(seed)
    device = base.resolve_device(config["system"]["device"])
    results_dir = config["system"]["results_dir"]
    os.makedirs(results_dir, exist_ok=True)

    print(f"Device: {device} | Seed: {seed}")
    for warning in warnings:
        print(f"WARNING: {warning}")

    data_cfg = config["data"]
    train_set, test_set, num_classes, in_channels = base.get_dataset(
        data_cfg["dataset"],
        data_cfg["data_root"],
    )
    test_loader = DataLoader(
        test_set,
        batch_size=int(config["evaluation"]["eval_batch_size"]),
        shuffle=False,
        num_workers=0,
    )
    client_dict = base._build_partition(train_set, config)
    partition_dir = os.path.join(results_dir, "partition")
    manifest_path, indices_path, partition_manifest = base.write_partition_artifacts(
        client_dict=client_dict,
        dataset=train_set,
        dataset_name=str(data_cfg["dataset"]),
        strategy=str(data_cfg["partition"]),
        seed=seed,
        parameters=base._partition_parameters(data_cfg),
        output_dir=partition_dir,
    )
    dist_path = base.plot_distribution(
        client_dict,
        train_set,
        num_classes,
        save_path=os.path.join(results_dir, "distribution.png"),
    )
    distribution_csv = base.write_client_distribution_csv(
        client_dict,
        train_set,
        os.path.join(results_dir, "client_distribution.csv"),
    )
    print(f"Partition plot saved -> {dist_path}")
    print(f"Client distribution saved -> {distribution_csv}")
    print(f"Partition manifest saved -> {manifest_path}")
    print(f"Exact partition indices saved -> {indices_path}")

    algorithm = str(config["algorithm"]["name"]).lower()
    if algorithm == "all":
        raise ValueError(
            "resumable local execution requires one concrete algorithm per execution"
        )
    summary = run_resumable_experiment(
        algorithm=algorithm,
        config=config,
        train_set=train_set,
        test_loader=test_loader,
        client_dict=client_dict,
        num_classes=num_classes,
        in_channels=in_channels,
        device=device,
        control=control,
    )
    summaries = [summary]
    run_csvs = {algorithm: summary["csv_path"]}
    plots = generate_all_plots(
        results_dir,
        run_csvs=run_csvs,
        dp_enabled=bool(config["dp"]["enabled"]),
    )
    for plot in plots:
        print(f"Plot saved -> {plot}")

    summary_md = base.build_summary_table(summaries, config)
    summary_path = os.path.join(results_dir, "summary.md")
    with open(summary_path, "w", encoding="utf-8") as handle:
        handle.write(summary_md + "\n")

    machine_summary_path = os.path.join(results_dir, "summary.json")
    machine_summary = {
        "schema_version": 1,
        "dataset": data_cfg["dataset"],
        "partition": partition_manifest,
        "seed": seed,
        "dp": {
            "enabled": bool(config["dp"]["enabled"]),
            "target_epsilon": config["dp"].get("target_epsilon"),
            "target_delta": config["dp"].get("target_delta"),
            "noise_multiplier": config["dp"].get("noise_multiplier"),
            "privacy_parameter_source": config["dp"].get(
                "privacy_parameter_source"
            ),
            "calibrated_epsilon": config["dp"].get("calibrated_epsilon"),
        },
        "runs": summaries,
        "resumed": control.resume,
    }
    with open(machine_summary_path, "w", encoding="utf-8") as handle:
        json.dump(base._json_safe(machine_summary), handle, indent=2, sort_keys=True)
        handle.write("\n")

    print("\n" + summary_md)
    print(f"\nSummary written -> {summary_path}")
    print(f"Machine summary written -> {machine_summary_path}")
    return summary_path
