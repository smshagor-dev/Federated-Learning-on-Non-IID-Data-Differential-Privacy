"""Algorithm Expansion phase local-training benchmark (Work Package R). See
docs/benchmarking.md's "the Algorithm Expansion phase: local algorithm training" section.

Measures wall-clock local-training duration for FedAvg (legacy adapter,
as a baseline reference), FedSAM, Ditto, and Per-FedAvg on two model
sizes (a tiny synthetic bridge model, and the real GroupNormCNN at
32x32). Real, locally-measured numbers only — no fabricated results; run
this script yourself to reproduce (`python scripts/benchmark_algorithms.py`).

This is a wall-clock `time.perf_counter` harness (warm-up + N timed
repetitions, median/mean reported), the same methodology
cpp/benchmarks/aggregation_benchmark.cpp uses, chosen for the same reason
(no benchmark-framework dependency needed for this scope).
"""

from __future__ import annotations

import csv
import statistics
import sys
import time
from pathlib import Path

import torch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python" / "src"))
sys.path.insert(0, str(_REPO_ROOT))

from fl_platform.algorithms import LocalTrainingContext, get_algorithm  # noqa: E402
from fl_platform.models.factory import build_model  # noqa: E402
from fl_platform.worker.dataset_loader import PartitionManifest  # noqa: E402
from fl_platform.worker.task_runner import build_bridge_compatible_model  # noqa: E402

REPETITIONS = 5
WARMUP_REPETITIONS = 1

MODEL_SIZES = {
    "tiny_bridge": {
        "num_classes": 3,
        "in_channels": 1,
        "image_size": 4,
        "sample_count": 64,
    },
    "groupnorm_cnn": {
        "num_classes": 10,
        "in_channels": 3,
        "image_size": 32,
        "sample_count": 64,
    },
}

ALGORITHM_CONFIGS = {
    "fedavg": {},
    "fedsam": {"rho": 0.05, "local_epochs": 1},
    "ditto": {
        "regularization_coefficient": 0.5,
        "global_local_epochs": 1,
        "personalized_local_epochs": 1,
    },
    "per_fedavg": {
        "inner_steps": 2,
        "meta_steps": 1,
        "minimum_samples_required": 4,
    },
}


def _build_model(model_size: str) -> torch.nn.Module:
    spec = MODEL_SIZES[model_size]
    if model_size == "tiny_bridge":
        return build_bridge_compatible_model(
            num_classes=spec["num_classes"],
            in_channels=spec["in_channels"],
            image_size=spec["image_size"],
        )
    return build_model(
        "groupnorm_cnn",
        num_classes=spec["num_classes"],
        in_channels=spec["in_channels"],
        image_size=spec["image_size"],
    )


def _make_context(
    algorithm: str, model_size: str, model: torch.nn.Module
) -> LocalTrainingContext:
    spec = MODEL_SIZES[model_size]
    partition = PartitionManifest(
        dataset_id="benchmark_synthetic",
        partition_id="bench-p1",
        client_id="bench-client",
        sample_count=spec["sample_count"],
        seed=1,
        num_classes=spec["num_classes"],
        in_channels=spec["in_channels"],
        image_size=spec["image_size"],
    )
    return LocalTrainingContext(
        run_id="bench-r1",
        round_id=1,
        client_id="bench-client",
        task_id="bench-t1",
        algorithm=algorithm,
        model_version="v0",
        global_model=model,
        dataset_partition=partition,
        device=torch.device("cpu"),
        seed=1,
        algorithm_config=dict(ALGORITHM_CONFIGS[algorithm]),
        optimizer_config={"batch_size": 16, "learning_rate": 0.01},
        evaluation_config={},
    )


def _time_one_run(algorithm: str, model_size: str) -> tuple[float, int]:
    model = _build_model(model_size)
    context = _make_context(algorithm, model_size, model)
    algo = get_algorithm(algorithm)
    algo.validate_task(context)
    started = time.perf_counter()
    result = algo.train(context)
    elapsed = time.perf_counter() - started
    return elapsed, result.sample_count


def run_benchmark() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for model_size in MODEL_SIZES:
        for algorithm in ALGORITHM_CONFIGS:
            for _ in range(WARMUP_REPETITIONS):
                _time_one_run(algorithm, model_size)
            timings = []
            sample_count = 0
            for _ in range(REPETITIONS):
                elapsed, sample_count = _time_one_run(algorithm, model_size)
                timings.append(elapsed)
            median_s = statistics.median(timings)
            mean_s = statistics.mean(timings)
            rows.append(
                {
                    "model_size": model_size,
                    "algorithm": algorithm,
                    "repetitions": REPETITIONS,
                    "sample_count": sample_count,
                    "median_ms": round(median_s * 1000, 3),
                    "mean_ms": round(mean_s * 1000, 3),
                    "samples_per_sec": round(sample_count / median_s, 1)
                    if median_s > 0
                    else 0.0,
                }
            )
            print(
                f"{model_size:14s} {algorithm:12s} median={median_s * 1000:8.3f}ms "
                f"mean={mean_s * 1000:8.3f}ms samples/sec={sample_count / median_s:.1f}"
            )
    return rows


def main() -> None:
    rows = run_benchmark()
    output_dir = Path(__file__).resolve().parent.parent / "benchmarks" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "algorithm_expansion_benchmark_latest.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {output_path}")


if __name__ == "__main__":
    main()
