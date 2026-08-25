"""Federated Learning Platform v3 foundations.

All modules remain subject to the explicit v3 release gates; presence in this
package is not a claim of production validation.
"""

from fl_platform.v3.adversarial_benchmark import (
    RobustnessBenchmarkSummary,
    RobustnessTrialConfig,
    RobustnessTrialResult,
    run_robustness_benchmark,
    run_robustness_trial,
)
from fl_platform.v3.algorithm_suite import (
    ParameterPartition,
    fedbn_partition,
    fednova_aggregate,
    fedrep_partition,
    moon_contrastive_loss,
    pfedme_personalized_step,
)
from fl_platform.v3.async_runtime import (
    AsyncModelState,
    AsyncUpdate,
    staleness_weight,
)
from fl_platform.v3.attacks import (
    AttackKind,
    apply_training_data_attack,
    apply_update_attack,
)
from fl_platform.v3.capabilities import CapabilityRequest, validate_capability_request
from fl_platform.v3.heterogeneity import ClientSystemProfile, EdgeRequirements
from fl_platform.v3.heterogeneous_execution import (
    HeterogeneityAdmissionPolicy,
    HeterogeneityEvaluation,
    HeterogeneityRoundMetrics,
)
from fl_platform.v3.observability import RobustnessMetrics, RoundMetrics
from fl_platform.v3.observability_runtime import (
    JsonlMetricSink,
    MetricEvent,
    MetricEventSink,
    V3MetricRegistry,
)
from fl_platform.v3.privacy_validation import (
    GradientLeakageResult,
    LedgerResumeValidation,
    MembershipInferenceResult,
    PrivacyUtilityPoint,
    PrivacyValidationReport,
    build_privacy_utility_curve,
    gradient_leakage_similarity,
    membership_inference_auc,
    validate_sample_ledger_resume,
)
from fl_platform.v3.release_gates import REQUIRED_V3_GATES, ReleaseGateReport
from fl_platform.v3.robust_aggregation import (
    coordinate_median,
    krum,
    multi_krum,
    trimmed_mean,
)
from fl_platform.v3.runtime_integration import (
    AggregationConfig,
    AggregationOutcome,
    V3AggregationEngine,
)
from fl_platform.v3.server_optimizers import AdaptiveServerOptimizer, OptimizerConfig
from fl_platform.v3.workloads import WORKLOADS, get_workload

__all__ = [
    "AdaptiveServerOptimizer",
    "AggregationConfig",
    "AggregationOutcome",
    "AsyncModelState",
    "AsyncUpdate",
    "AttackKind",
    "CapabilityRequest",
    "ClientSystemProfile",
    "EdgeRequirements",
    "GradientLeakageResult",
    "HeterogeneityAdmissionPolicy",
    "HeterogeneityEvaluation",
    "HeterogeneityRoundMetrics",
    "JsonlMetricSink",
    "LedgerResumeValidation",
    "MembershipInferenceResult",
    "MetricEvent",
    "MetricEventSink",
    "OptimizerConfig",
    "ParameterPartition",
    "PrivacyUtilityPoint",
    "PrivacyValidationReport",
    "REQUIRED_V3_GATES",
    "ReleaseGateReport",
    "RobustnessBenchmarkSummary",
    "RobustnessMetrics",
    "RobustnessTrialConfig",
    "RobustnessTrialResult",
    "RoundMetrics",
    "V3AggregationEngine",
    "V3MetricRegistry",
    "WORKLOADS",
    "apply_training_data_attack",
    "apply_update_attack",
    "build_privacy_utility_curve",
    "coordinate_median",
    "fedbn_partition",
    "fednova_aggregate",
    "fedrep_partition",
    "get_workload",
    "gradient_leakage_similarity",
    "krum",
    "membership_inference_auc",
    "moon_contrastive_loss",
    "multi_krum",
    "pfedme_personalized_step",
    "run_robustness_benchmark",
    "run_robustness_trial",
    "staleness_weight",
    "trimmed_mean",
    "validate_capability_request",
    "validate_sample_ledger_resume",
]
