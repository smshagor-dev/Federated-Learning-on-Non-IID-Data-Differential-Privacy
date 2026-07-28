from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from fl_platform.algorithms.registry import registered_algorithm_names
from fl_platform.datasets.registry import DatasetRegistry
from fl_platform.privacy.compatibility import (
    is_usable,
    sample_level_status,
    user_level_status,
)


class ExperimentSpecificationError(ValueError):
    """Structured validation failure for research experiment specifications."""


class PartitionStrategy(StrEnum):
    IID = "iid"
    DIRICHLET = "dirichlet"
    PATHOLOGICAL = "pathological"
    QUANTITY_SKEW = "quantity_skew"


class PrivacyMode(StrEnum):
    NONE = "none"
    SAMPLE_LEVEL = "sample_level_dp"
    USER_LEVEL = "user_level_dp"
    HYBRID = "hybrid_dp"


class SecureAggregationProvider(StrEnum):
    NONE = "none"
    SECAGG_NO_DROPOUT_EXPERIMENTAL = "SECAGG_NO_DROPOUT_EXPERIMENTAL"


class AdaptiveClippingMode(StrEnum):
    DISABLED = "disabled"
    ENABLED = "enabled"


class DeterminismLevel(StrEnum):
    STRICT_CPU = "STRICT_CPU"
    BEST_EFFORT_ACCELERATOR = "BEST_EFFORT_ACCELERATOR"
    PERFORMANCE = "PERFORMANCE"


@dataclass(slots=True)
class DatasetConfiguration:
    dataset_id: str
    dataset_version: str
    dataset_checksum: str
    split_seed: int
    train_split_fraction: float
    validation_split_fraction: float
    test_split_fraction: float
    preprocessing_configuration: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PartitionConfiguration:
    strategy: PartitionStrategy
    num_clients: int
    seed: int
    minimum_client_samples: int
    alpha: float | None = None
    classes_per_client: int | None = None
    quantity_skew_sigma: float | None = None
    partition_manifest_hash: str = ""


@dataclass(slots=True)
class ModelConfiguration:
    model_id: str
    model_version: str
    initialization_seed: int


@dataclass(slots=True)
class AlgorithmConfiguration:
    algorithm_id: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PrivacyConfiguration:
    privacy_mode: PrivacyMode
    noise_multiplier: float | None = None
    target_delta: float | None = None
    user_level_clip_norm: float | None = None
    sample_level_max_grad_norm: float | None = None
    epsilon_budget: float | None = None
    combined_epsilon: float | None = None
    client_weighting: str = "uniform"


@dataclass(slots=True)
class SecureAggregationConfiguration:
    provider: SecureAggregationProvider
    dropout_recovery_requested: bool = False


@dataclass(slots=True)
class AdaptiveClippingConfiguration:
    mode: AdaptiveClippingMode
    initial_bound: float | None = None
    min_bound: float | None = None
    max_bound: float | None = None
    target_quantile: float | None = None
    learning_rate: float | None = None
    indicator_noise_multiplier: float | None = None


@dataclass(slots=True)
class RuntimeLimits:
    max_rounds: int
    local_epochs: int
    batch_size: int
    learning_rate: float
    evaluation_frequency: int
    selected_clients_per_round: int


@dataclass(slots=True)
class SeedConfiguration:
    seeds: list[int]
    partition_seed: int
    worker_assignment_seed: int
    coordinator_seed: int


@dataclass(slots=True)
class ExperimentSpecification:
    schema_version: int
    experiment_id: str
    experiment_name: str
    research_question: str
    dataset: DatasetConfiguration
    partition: PartitionConfiguration
    model: ModelConfiguration
    algorithm: AlgorithmConfiguration
    privacy: PrivacyConfiguration
    secure_aggregation: SecureAggregationConfiguration
    adaptive_clipping: AdaptiveClippingConfiguration
    runtime: RuntimeLimits
    seeds: SeedConfiguration
    determinism_level: DeterminismLevel
    tags: list[str] = field(default_factory=list)
    creation_timestamp: str = ""
    specification_hash: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["partition"]["strategy"] = self.partition.strategy.value
        payload["privacy"]["privacy_mode"] = self.privacy.privacy_mode.value
        payload["secure_aggregation"]["provider"] = (
            self.secure_aggregation.provider.value
        )
        payload["adaptive_clipping"]["mode"] = self.adaptive_clipping.mode.value
        payload["determinism_level"] = self.determinism_level.value
        payload["specification_hash"] = ""
        return payload

    def compute_hash(self) -> str:
        canonical = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def with_computed_hash(self) -> ExperimentSpecification:
        clone = ExperimentSpecification(**asdict(self))
        clone.specification_hash = clone.compute_hash()
        return clone


def validate_experiment_specification(
    specification: ExperimentSpecification,
) -> ExperimentSpecification:
    _validate_dataset(specification)
    _validate_partition(specification)
    _validate_algorithm(specification)
    _validate_privacy(specification)
    _validate_secure_aggregation(specification)
    _validate_adaptive_clipping(specification)
    _validate_runtime(specification)
    _validate_seeds(specification)
    expected_hash = specification.compute_hash()
    if (
        specification.specification_hash
        and specification.specification_hash != expected_hash
    ):
        raise ExperimentSpecificationError(
            "specification_hash does not match the canonical "
            "experiment specification payload"
        )
    specification.specification_hash = expected_hash
    return specification


def _validate_dataset(specification: ExperimentSpecification) -> None:
    dataset = specification.dataset
    registry = DatasetRegistry.with_default_registry()
    if dataset.dataset_id not in registry.list_names():
        raise ExperimentSpecificationError(f"unknown dataset '{dataset.dataset_id}'")
    if not dataset.dataset_version:
        raise ExperimentSpecificationError("dataset_version is required")
    if not dataset.dataset_checksum:
        raise ExperimentSpecificationError("dataset_checksum is required")
    total = (
        dataset.train_split_fraction
        + dataset.validation_split_fraction
        + dataset.test_split_fraction
    )
    if abs(total - 1.0) > 1e-9:
        raise ExperimentSpecificationError(
            "train/validation/test split fractions must sum to 1.0"
        )
    for name, value in [
        ("train_split_fraction", dataset.train_split_fraction),
        ("validation_split_fraction", dataset.validation_split_fraction),
        ("test_split_fraction", dataset.test_split_fraction),
    ]:
        if value < 0.0 or value > 1.0:
            raise ExperimentSpecificationError(f"{name} must be in [0, 1]")


def _validate_partition(specification: ExperimentSpecification) -> None:
    partition = specification.partition
    if partition.num_clients < 3:
        raise ExperimentSpecificationError("num_clients must be at least 3")
    if partition.minimum_client_samples <= 0:
        raise ExperimentSpecificationError("minimum_client_samples must be positive")
    if partition.strategy == PartitionStrategy.DIRICHLET and (
        partition.alpha is None or partition.alpha <= 0.0
    ):
        raise ExperimentSpecificationError(
            "dirichlet partition requires positive alpha"
        )
    if partition.strategy == PartitionStrategy.PATHOLOGICAL and (
        partition.classes_per_client is None or partition.classes_per_client <= 0
    ):
        raise ExperimentSpecificationError(
            "pathological partition requires positive classes_per_client"
        )
    if partition.strategy == PartitionStrategy.QUANTITY_SKEW and (
        partition.quantity_skew_sigma is None or partition.quantity_skew_sigma < 0.0
    ):
        raise ExperimentSpecificationError(
            "quantity_skew partition requires non-negative quantity_skew_sigma"
        )
    if not specification.partition.partition_manifest_hash:
        raise ExperimentSpecificationError("partition_manifest_hash is required")


def _validate_algorithm(specification: ExperimentSpecification) -> None:
    algorithm = specification.algorithm.algorithm_id
    if algorithm not in registered_algorithm_names():
        raise ExperimentSpecificationError(
            f"algorithm '{algorithm}' is unavailable in the current working tree"
        )


def _validate_privacy(specification: ExperimentSpecification) -> None:
    privacy = specification.privacy
    algorithm = specification.algorithm.algorithm_id
    if privacy.combined_epsilon is not None:
        raise ExperimentSpecificationError(
            "combined_epsilon is forbidden: keep sample-level "
            "and user-level epsilon separate"
        )
    if privacy.privacy_mode == PrivacyMode.SAMPLE_LEVEL:
        status = sample_level_status(algorithm)
        if not is_usable(status.status):
            raise ExperimentSpecificationError(
                "sample-level DP is "
                f"{status.status.value} for '{algorithm}': {status.reason}"
            )
    if privacy.privacy_mode in (PrivacyMode.USER_LEVEL, PrivacyMode.HYBRID):
        status = user_level_status(algorithm)
        if not is_usable(status.status):
            raise ExperimentSpecificationError(
                "user-level DP is "
                f"{status.status.value} for '{algorithm}': {status.reason}"
            )
        if privacy.client_weighting != "uniform":
            raise ExperimentSpecificationError(
                "USER_LEVEL_DP and HYBRID require uniform client weighting "
                "under this repository's trust model"
            )


def _validate_secure_aggregation(specification: ExperimentSpecification) -> None:
    secure_aggregation = specification.secure_aggregation
    algorithm = specification.algorithm.algorithm_id
    if secure_aggregation.dropout_recovery_requested:
        raise ExperimentSpecificationError(
            "dropout recovery remains blocked after the threshold-dependency decision"
        )
    if (
        secure_aggregation.provider
        == SecureAggregationProvider.SECAGG_NO_DROPOUT_EXPERIMENTAL
        and algorithm != "fedavg"
    ):
        raise ExperimentSpecificationError(
            "SECAGG_NO_DROPOUT_EXPERIMENTAL supports only fedavg "
            "in the current implementation"
        )


def _validate_adaptive_clipping(specification: ExperimentSpecification) -> None:
    adaptive = specification.adaptive_clipping
    if adaptive.mode == AdaptiveClippingMode.DISABLED:
        return
    if specification.privacy.privacy_mode not in (
        PrivacyMode.USER_LEVEL,
        PrivacyMode.HYBRID,
    ):
        raise ExperimentSpecificationError(
            "adaptive clipping requires USER_LEVEL_DP or HYBRID"
        )
    if adaptive.initial_bound is None or adaptive.initial_bound <= 0.0:
        raise ExperimentSpecificationError(
            "adaptive clipping initial_bound must be positive"
        )
    if adaptive.min_bound is None or adaptive.max_bound is None:
        raise ExperimentSpecificationError(
            "adaptive clipping requires min_bound and max_bound"
        )
    if adaptive.min_bound <= 0.0 or adaptive.min_bound > adaptive.max_bound:
        raise ExperimentSpecificationError("adaptive clipping bounds are invalid")


def _validate_runtime(specification: ExperimentSpecification) -> None:
    runtime = specification.runtime
    if runtime.max_rounds <= 0:
        raise ExperimentSpecificationError("max_rounds must be positive")
    if runtime.local_epochs <= 0:
        raise ExperimentSpecificationError("local_epochs must be positive")
    if runtime.batch_size <= 0:
        raise ExperimentSpecificationError("batch_size must be positive")
    if runtime.learning_rate <= 0.0:
        raise ExperimentSpecificationError("learning_rate must be positive")
    if runtime.evaluation_frequency <= 0:
        raise ExperimentSpecificationError("evaluation_frequency must be positive")
    if runtime.selected_clients_per_round <= 0:
        raise ExperimentSpecificationError(
            "selected_clients_per_round must be positive"
        )


def _validate_seeds(specification: ExperimentSpecification) -> None:
    if not specification.seeds.seeds:
        raise ExperimentSpecificationError("at least one experiment seed is required")
