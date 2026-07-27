"""Model registry, construction, and shared-backbone/personalization-head
support. See docs/model-registry.md and docs/shared-backbone-local-head.md."""

from .factory import (
    PERSONALIZED_PREFIXES,
    SHARED_PREFIXES,
    PersonalizableBridgeModel,
    build_model,
)
from .model_registry import (
    FilesystemModelRegistry,
    ModelRegistryEntry,
    ModelRegistryError,
    ModelStatus,
)
from .personalization import (
    ModelMetadata,
    apply_partial_state,
    compute_schema_hash,
    describe_model,
)
from .registry import ModelDescriptor, ModelRegistry

__all__ = [
    "FilesystemModelRegistry",
    "ModelDescriptor",
    "ModelMetadata",
    "ModelRegistry",
    "ModelRegistryEntry",
    "ModelRegistryError",
    "ModelStatus",
    "PERSONALIZED_PREFIXES",
    "PersonalizableBridgeModel",
    "SHARED_PREFIXES",
    "apply_partial_state",
    "build_model",
    "compute_schema_hash",
    "describe_model",
]
