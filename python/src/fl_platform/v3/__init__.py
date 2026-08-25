"""Federated Learning Platform v3 foundations.

All modules remain subject to the explicit v3 release gates; presence in this
package is not a claim of production validation.

The public v3 namespace is intentionally lazy. Importing a lightweight module
such as ``fl_platform.v3.release_security`` or ``fl_platform.v3.edge_runtime``
must not eagerly import optional training/privacy stacks or legacy simulator
packages. Public symbols retain the same import surface and are resolved only
when accessed.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "EDGE_CODEC": ("fl_platform.v3.edge_runtime", "EDGE_CODEC"),
    "AdaptiveServerOptimizer": (
        "fl_platform.v3.server_optimizers",
        "AdaptiveServerOptimizer",
    ),
    "AggregationConfig": ("fl_platform.v3.runtime_integration", "AggregationConfig"),
    "AggregationOutcome": (
        "fl_platform.v3.runtime_integration",
        "AggregationOutcome",
    ),
    "AsyncApplyResult": ("fl_platform.v3.async_runtime", "AsyncApplyResult"),
    "AsyncExecutionError": ("fl_platform.v3.async_execution", "AsyncExecutionError"),
    "AsyncModelState": ("fl_platform.v3.async_runtime", "AsyncModelState"),
    "AsyncResultOutcome": ("fl_platform.v3.async_execution", "AsyncResultOutcome"),
    "AsyncStateSnapshot": ("fl_platform.v3.async_runtime", "AsyncStateSnapshot"),
    "AsyncStateStore": ("fl_platform.v3.async_checkpoint", "AsyncStateStore"),
    "AsyncStateStoreError": (
        "fl_platform.v3.async_checkpoint",
        "AsyncStateStoreError",
    ),
    "AsyncUpdate": ("fl_platform.v3.async_runtime", "AsyncUpdate"),
    "AttackKind": ("fl_platform.v3.attacks", "AttackKind"),
    "CapabilityRequest": ("fl_platform.v3.capabilities", "CapabilityRequest"),
    "ClientLease": ("fl_platform.v3.async_membership", "ClientLease"),
    "ClientSystemProfile": ("fl_platform.v3.heterogeneity", "ClientSystemProfile"),
    "DurableAsyncResultProcessor": (
        "fl_platform.v3.async_execution",
        "DurableAsyncResultProcessor",
    ),
    "EdgeRequirements": ("fl_platform.v3.heterogeneity", "EdgeRequirements"),
    "EdgeRuntimeBudget": ("fl_platform.v3.edge_runtime", "EdgeRuntimeBudget"),
    "EdgeRuntimeError": ("fl_platform.v3.edge_runtime", "EdgeRuntimeError"),
    "EdgeTrainingResult": ("fl_platform.v3.edge_runtime", "EdgeTrainingResult"),
    "EdgeUpdatePayload": ("fl_platform.v3.edge_runtime", "EdgeUpdatePayload"),
    "EdgeWorkerRuntime": ("fl_platform.v3.edge_runtime", "EdgeWorkerRuntime"),
    "ElasticClientRegistry": (
        "fl_platform.v3.async_membership",
        "ElasticClientRegistry",
    ),
    "ElasticMembershipSnapshot": (
        "fl_platform.v3.async_membership",
        "ElasticMembershipSnapshot",
    ),
    "GradientLeakageResult": (
        "fl_platform.v3.privacy_validation",
        "GradientLeakageResult",
    ),
    "HeterogeneityAdmissionPolicy": (
        "fl_platform.v3.heterogeneous_execution",
        "HeterogeneityAdmissionPolicy",
    ),
    "HeterogeneityEvaluation": (
        "fl_platform.v3.heterogeneous_execution",
        "HeterogeneityEvaluation",
    ),
    "HeterogeneityRoundMetrics": (
        "fl_platform.v3.heterogeneous_execution",
        "HeterogeneityRoundMetrics",
    ),
    "Int8UpdateCodec": ("fl_platform.v3.edge_runtime", "Int8UpdateCodec"),
    "JsonlMetricSink": ("fl_platform.v3.observability_runtime", "JsonlMetricSink"),
    "LedgerResumeValidation": (
        "fl_platform.v3.privacy_validation",
        "LedgerResumeValidation",
    ),
    "MembershipInferenceResult": (
        "fl_platform.v3.privacy_validation",
        "MembershipInferenceResult",
    ),
    "MetricEvent": ("fl_platform.v3.observability_runtime", "MetricEvent"),
    "MetricEventSink": ("fl_platform.v3.observability_runtime", "MetricEventSink"),
    "OptimizerConfig": ("fl_platform.v3.server_optimizers", "OptimizerConfig"),
    "ParameterPartition": ("fl_platform.v3.algorithm_suite", "ParameterPartition"),
    "PrivacyUtilityPoint": (
        "fl_platform.v3.privacy_validation",
        "PrivacyUtilityPoint",
    ),
    "PrivacyValidationReport": (
        "fl_platform.v3.privacy_validation",
        "PrivacyValidationReport",
    ),
    "REQUIRED_V3_GATES": ("fl_platform.v3.release_gates", "REQUIRED_V3_GATES"),
    "ReleaseGateReport": ("fl_platform.v3.release_gates", "ReleaseGateReport"),
    "RobustnessBenchmarkSummary": (
        "fl_platform.v3.adversarial_benchmark",
        "RobustnessBenchmarkSummary",
    ),
    "RobustnessMetrics": ("fl_platform.v3.observability", "RobustnessMetrics"),
    "RobustnessTrialConfig": (
        "fl_platform.v3.adversarial_benchmark",
        "RobustnessTrialConfig",
    ),
    "RobustnessTrialResult": (
        "fl_platform.v3.adversarial_benchmark",
        "RobustnessTrialResult",
    ),
    "RoundMetrics": ("fl_platform.v3.observability", "RoundMetrics"),
    "V3AggregationEngine": (
        "fl_platform.v3.runtime_integration",
        "V3AggregationEngine",
    ),
    "V3MetricRegistry": (
        "fl_platform.v3.observability_runtime",
        "V3MetricRegistry",
    ),
    "WORKLOADS": ("fl_platform.v3.workloads", "WORKLOADS"),
    "apply_training_data_attack": (
        "fl_platform.v3.attacks",
        "apply_training_data_attack",
    ),
    "apply_update_attack": ("fl_platform.v3.attacks", "apply_update_attack"),
    "build_privacy_utility_curve": (
        "fl_platform.v3.privacy_validation",
        "build_privacy_utility_curve",
    ),
    "coordinate_median": (
        "fl_platform.v3.robust_aggregation",
        "coordinate_median",
    ),
    "fedbn_partition": ("fl_platform.v3.algorithm_suite", "fedbn_partition"),
    "fednova_aggregate": ("fl_platform.v3.algorithm_suite", "fednova_aggregate"),
    "fedrep_partition": ("fl_platform.v3.algorithm_suite", "fedrep_partition"),
    "get_workload": ("fl_platform.v3.workloads", "get_workload"),
    "gradient_leakage_similarity": (
        "fl_platform.v3.privacy_validation",
        "gradient_leakage_similarity",
    ),
    "krum": ("fl_platform.v3.robust_aggregation", "krum"),
    "membership_inference_auc": (
        "fl_platform.v3.privacy_validation",
        "membership_inference_auc",
    ),
    "moon_contrastive_loss": (
        "fl_platform.v3.algorithm_suite",
        "moon_contrastive_loss",
    ),
    "multi_krum": ("fl_platform.v3.robust_aggregation", "multi_krum"),
    "pfedme_personalized_step": (
        "fl_platform.v3.algorithm_suite",
        "pfedme_personalized_step",
    ),
    "run_robustness_benchmark": (
        "fl_platform.v3.adversarial_benchmark",
        "run_robustness_benchmark",
    ),
    "run_robustness_trial": (
        "fl_platform.v3.adversarial_benchmark",
        "run_robustness_trial",
    ),
    "staleness_weight": ("fl_platform.v3.async_runtime", "staleness_weight"),
    "trimmed_mean": ("fl_platform.v3.robust_aggregation", "trimmed_mean"),
    "validate_capability_request": (
        "fl_platform.v3.capabilities",
        "validate_capability_request",
    ),
    "validate_sample_ledger_resume": (
        "fl_platform.v3.privacy_validation",
        "validate_sample_ledger_resume",
    ),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
