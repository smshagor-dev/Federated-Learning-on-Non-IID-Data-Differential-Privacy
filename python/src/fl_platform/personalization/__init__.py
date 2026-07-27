"""Personalization/fairness metrics and the personalized model store. See
docs/fairness-metrics.md and docs/personalized-model-store.md."""

from .metrics import (
    PerClientEvaluationRecord,
    PersonalizationMetrics,
    compute_aggregated_personalization_metrics,
    summarize_personalization,
)
from .store import (
    CURRENT_SCHEMA_VERSION,
    FilesystemPersonalizedModelStore,
    PersonalizedModelCache,
    PersonalizedModelCorruptionError,
    PersonalizedModelOwnershipError,
    PersonalizedModelRecord,
    PersonalizedModelSchemaError,
    PersonalizedModelStoreError,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "FilesystemPersonalizedModelStore",
    "PerClientEvaluationRecord",
    "PersonalizationMetrics",
    "PersonalizedModelCache",
    "PersonalizedModelCorruptionError",
    "PersonalizedModelOwnershipError",
    "PersonalizedModelRecord",
    "PersonalizedModelSchemaError",
    "PersonalizedModelStoreError",
    "compute_aggregated_personalization_metrics",
    "summarize_personalization",
]
