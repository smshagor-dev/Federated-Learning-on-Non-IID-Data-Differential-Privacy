"""Python-to-C++ aggregation compatibility adapter."""

from fl_platform.compat.cpp_bridge import (
    CppAggregationError,
    CppAggregationRequest,
    CppAggregationResult,
    CppAggregationUnavailable,
    CppClientUpdate,
    apply_delta_to_state_dict,
    find_cli,
    run_cpp_aggregate,
)

__all__ = [
    "CppAggregationError",
    "CppAggregationRequest",
    "CppAggregationResult",
    "CppAggregationUnavailable",
    "CppClientUpdate",
    "apply_delta_to_state_dict",
    "find_cli",
    "run_cpp_aggregate",
]
