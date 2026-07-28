"""Research evaluation specifications, validation, and reproducibility helpers."""

from .command_auth import StaticBearerCommandAuthenticator
from .command_server import ResearchCommandHTTPServer
from .command_service import ResearchCommandService
from .registry import (
    BoundedExperimentOrchestrator,
    EnvironmentManifest,
    ExperimentConflictError,
    ExperimentCorruptionError,
    ExperimentRegistry,
    ExperimentRegistryRecord,
    ExperimentRunRecord,
    ExperimentState,
    RunState,
    SyntheticExecutionAdapter,
    SyntheticExecutionResult,
    build_environment_manifest,
)
from .specification import (
    AdaptiveClippingMode,
    DeterminismLevel,
    ExperimentSpecification,
    ExperimentSpecificationError,
    PartitionStrategy,
    PrivacyMode,
    SecureAggregationProvider,
    validate_experiment_specification,
)

__all__ = [
    "AdaptiveClippingMode",
    "BoundedExperimentOrchestrator",
    "DeterminismLevel",
    "EnvironmentManifest",
    "ExperimentConflictError",
    "ExperimentCorruptionError",
    "ExperimentRegistry",
    "ExperimentRegistryRecord",
    "ExperimentRunRecord",
    "ExperimentSpecification",
    "ExperimentSpecificationError",
    "ExperimentState",
    "PartitionStrategy",
    "PrivacyMode",
    "ResearchCommandHTTPServer",
    "ResearchCommandService",
    "RunState",
    "SecureAggregationProvider",
    "StaticBearerCommandAuthenticator",
    "SyntheticExecutionAdapter",
    "SyntheticExecutionResult",
    "build_environment_manifest",
    "validate_experiment_specification",
]
