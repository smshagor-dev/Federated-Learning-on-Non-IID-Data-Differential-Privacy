"""Federated Learning Platform v3 foundations.

All modules remain subject to the explicit v3 release gates; presence in this
package is not a claim of production validation.
"""

from fl_platform.v3.async_runtime import AsyncModelState, AsyncUpdate, staleness_weight
from fl_platform.v3.capabilities import CapabilityRequest, validate_capability_request
from fl_platform.v3.heterogeneity import ClientSystemProfile, EdgeRequirements
from fl_platform.v3.observability import RobustnessMetrics, RoundMetrics
from fl_platform.v3.release_gates import REQUIRED_V3_GATES, ReleaseGateReport
from fl_platform.v3.robust_aggregation import coordinate_median, krum, multi_krum, trimmed_mean
from fl_platform.v3.server_optimizers import AdaptiveServerOptimizer, OptimizerConfig
from fl_platform.v3.workloads import WORKLOADS, get_workload

__all__ = [
    "AdaptiveServerOptimizer",
    "AsyncModelState",
    "AsyncUpdate",
    "CapabilityRequest",
    "ClientSystemProfile",
    "EdgeRequirements",
    "OptimizerConfig",
    "REQUIRED_V3_GATES",
    "ReleaseGateReport",
    "RobustnessMetrics",
    "RoundMetrics",
    "WORKLOADS",
    "coordinate_median",
    "get_workload",
    "krum",
    "multi_krum",
    "staleness_weight",
    "trimmed_mean",
    "validate_capability_request",
]
