#!/usr/bin/env python3
"""Run deterministic Python-layer v3 chaos soak validation and emit JSON."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from fl_platform.v3.chaos_reliability import (
    ChaosProfile,
    ChaosRoundExecutor,
    DeterministicChaosPlan,
)
from fl_platform.workers import TrainingResult, TrainingTask, WorkerService


class SoakTrainer:
    def train(self, task: TrainingTask) -> TrainingResult:
        return TrainingResult(
            run_id=task.run_id,
            round_id=task.round_id,
            client_id=task.client_id,
            model_version=task.model_version,
            sample_count=16,
            local_step_count=4,
            model_update=(0.25, -0.5, 0.75),
        )


def _tasks(client_count: int) -> tuple[TrainingTask, ...]:
    return tuple(
        TrainingTask(
            run_id="v3-chaos-soak",
            round_id=1,
            client_id=f"client-{index:04d}",
            model_version="model-v1",
            algorithm="fedavg",
        )
        for index in range(client_count)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=250)
    parser.add_argument("--clients", type=int, default=32)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seeds <= 0 or args.clients <= 0:
        raise SystemExit("--seeds and --clients must be positive")

    profile = ChaosProfile(
        drop_probability=0.12,
        transient_crash_probability=0.12,
        permanent_crash_probability=0.06,
        delay_probability=0.15,
        duplicate_replay_probability=0.15,
    )
    tasks = _tasks(args.clients)
    total_recovered = 0
    total_dropped = 0
    total_failed = 0
    total_delayed = 0
    total_retries = 0
    total_replays = 0
    recovery_rates: list[float] = []

    for seed in range(args.seeds):
        result = ChaosRoundExecutor(
            WorkerService(SoakTrainer(), worker_id="chaos-soak-worker"),
            DeterministicChaosPlan(seed=seed, profile=profile),
            max_retries=1,
        ).run(tasks)
        result.validate_invariants()
        total_recovered += result.recovered_clients
        total_dropped += len(result.dropped_clients)
        total_failed += len(result.failed_clients)
        total_delayed += len(result.delayed_clients)
        total_retries += result.retry_attempts
        total_replays += result.replay_rejections
        recovery_rates.append(result.recovery_rate)

    selected = args.seeds * args.clients
    if total_recovered + total_dropped + total_failed != selected:
        raise SystemExit("chaos soak accounting mismatch")

    payload = {
        "schema_version": 1,
        "kind": "v3-python-chaos-soak",
        "seeds": args.seeds,
        "clients_per_round": args.clients,
        "selected_clients": selected,
        "recovered_clients": total_recovered,
        "dropped_clients": total_dropped,
        "failed_clients": total_failed,
        "delayed_results": total_delayed,
        "retry_attempts": total_retries,
        "duplicate_replay_rejections": total_replays,
        "mean_recovery_rate": statistics.fmean(recovery_rates),
        "minimum_recovery_rate": min(recovery_rates),
        "maximum_recovery_rate": max(recovery_rates),
        "invariant_failures": 0,
        "scope": "python-worker-shell; not distributed-process/network chaos evidence",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
