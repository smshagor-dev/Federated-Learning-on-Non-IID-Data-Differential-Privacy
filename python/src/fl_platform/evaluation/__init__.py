"""Global and per-client model evaluation. See docs/personalized-evaluation.md."""

from .service import (
    GlobalEvaluationResult,
    evaluate_global_model,
    evaluate_model_on_partition,
)

__all__ = [
    "GlobalEvaluationResult",
    "evaluate_global_model",
    "evaluate_model_on_partition",
]
