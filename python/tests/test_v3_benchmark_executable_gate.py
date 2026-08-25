from fl_platform.benchmark.matrix import BenchmarkPartition
from fl_platform.benchmark.v3_matrix import (
    HeterogeneityCondition,
    PrivacyCondition,
    build_v3_benchmark_plan,
)
from fl_platform.v3.attacks import AttackKind


def test_primitive_only_algorithm_is_not_marked_benchmark_runnable() -> None:
    plan = build_v3_benchmark_plan(
        benchmark_id="worker-registry-gate",
        datasets=("mnist",),
        algorithms=("fednova",),
        seeds=(1, 2, 3),
        partitions=(BenchmarkPartition("iid", "iid", {}),),
        privacy_conditions=(PrivacyCondition("non-private", None),),
        attacks=(AttackKind.NONE,),
        aggregation_strategies=("mean",),
        heterogeneity_conditions=(
            HeterogeneityCondition("nominal", "baseline"),
        ),
        minimum_replicates=3,
    )
    cells = plan.expand(minimum_replicates=3)
    assert all(not cell.runnable for cell in cells)
    assert all(
        cell.exclusion_reason == "algorithm is not registered in canonical worker runtime"
        for cell in cells
    )
