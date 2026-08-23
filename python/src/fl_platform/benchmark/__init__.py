"""Benchmark planning, metrics, statistics, and result aggregation."""

from .heterogeneity import HeterogeneityVector, compute_heterogeneity_vector
from .matrix import (
    BenchmarkCell,
    BenchmarkPartition,
    BenchmarkPlan,
    build_benchmark_plan,
    standard_partition_conditions,
)
from .results import (
    AlgorithmComparisonRow,
    BenchmarkObservation,
    BenchmarkSummaryRow,
    compare_algorithms,
    summarize_observations,
    validate_observations,
)
from .statistics import (
    MetricSummary,
    PairedComparison,
    bootstrap_mean_interval,
    compare_paired_metrics,
    holm_adjust,
    summarize_metric,
)

__all__ = [
    "AlgorithmComparisonRow",
    "BenchmarkCell",
    "BenchmarkObservation",
    "BenchmarkPartition",
    "BenchmarkPlan",
    "BenchmarkSummaryRow",
    "HeterogeneityVector",
    "MetricSummary",
    "PairedComparison",
    "bootstrap_mean_interval",
    "build_benchmark_plan",
    "compare_algorithms",
    "compare_paired_metrics",
    "compute_heterogeneity_vector",
    "holm_adjust",
    "standard_partition_conditions",
    "summarize_metric",
    "summarize_observations",
    "validate_observations",
]
