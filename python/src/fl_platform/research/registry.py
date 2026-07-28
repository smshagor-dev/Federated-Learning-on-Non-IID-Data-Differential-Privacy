from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from .specification import (
    AdaptiveClippingConfiguration,
    AdaptiveClippingMode,
    AlgorithmConfiguration,
    DatasetConfiguration,
    DeterminismLevel,
    ExperimentSpecification,
    ModelConfiguration,
    PartitionConfiguration,
    PartitionStrategy,
    PrivacyConfiguration,
    PrivacyMode,
    RuntimeLimits,
    SecureAggregationConfiguration,
    SecureAggregationProvider,
    SeedConfiguration,
    validate_experiment_specification,
)

EXPERIMENT_REGISTRY_SCHEMA_VERSION = 1
RUN_RECORD_SCHEMA_VERSION = 1
EVENT_RECORD_SCHEMA_VERSION = 1
METRIC_RECORD_SCHEMA_VERSION = 1
ARTIFACT_MANIFEST_SCHEMA_VERSION = 1
ENVIRONMENT_MANIFEST_SCHEMA_VERSION = 1

_SAFE_EXPERIMENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_SAFE_ARTIFACT_PATH_RE = re.compile(r"^[A-Za-z0-9._/\-]+$")
_PROHIBITED_ARTIFACT_TEXT = (
    "dataset sample",
    "clear update",
    "individual update norm",
    "adaptive indicator",
    "private key",
    "shared secret",
    "noise seed",
    "access token",
    "password",
)
_ALLOWED_METRIC_SCOPES = frozenset(
    {
        "GLOBAL",
        "ROUND",
        "AGGREGATE_CLIENT",
        "PRIVACY",
        "SECURE_AGGREGATION",
        "RUNTIME",
    }
)
_ALLOWED_METRIC_NAMES = frozenset(
    {
        "train_loss",
        "validation_loss",
        "test_loss",
        "accuracy",
        "macro_precision",
        "macro_recall",
        "macro_f1",
        "round_duration_seconds",
        "communication_bytes",
        "coordinator_cpu_seconds",
        "worker_cpu_seconds",
        "peak_memory_bytes",
        "participating_clients",
        "successful_clients",
        "failed_clients",
        "sample_epsilon",
        "sample_delta",
        "user_epsilon",
        "user_delta",
        "secure_handshake_duration_seconds",
        "secure_encoding_duration_seconds",
        "secure_masking_duration_seconds",
        "secure_finalization_duration_seconds",
        "adaptive_clip_bound",
        "adaptive_clip_state_version",
    }
)


class ResearchRegistryError(RuntimeError):
    """Base error for durable research registry operations."""


class ExperimentConflictError(ResearchRegistryError):
    """Raised when a version or idempotency conflict is detected."""


class ExperimentCorruptionError(ResearchRegistryError):
    """Raised when persisted research state fails integrity checks."""


class ArtifactSanitizationError(ResearchRegistryError):
    """Raised when an artifact payload or path is unsafe to register."""


class ExperimentState(StrEnum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    PREPARING = "PREPARING"
    READY = "READY"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELED = "CANCELED"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_PARTIAL_RUNS = "COMPLETED_WITH_PARTIAL_RUNS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CORRUPTED = "CORRUPTED"


class RunState(StrEnum):
    CREATED = "CREATED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    EVALUATING = "EVALUATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    BLOCKED = "BLOCKED"
    LOST = "LOST"
    CORRUPTED = "CORRUPTED"


class InclusionStatus(StrEnum):
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(payload: str) -> str:
    return _sha256_bytes(payload.encode("utf-8"))


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _atomic_write_text(path: Path, payload: str, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and path.exists():
        raise ExperimentConflictError(f"refusing to overwrite immutable file {path}")
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(payload, encoding="utf-8")
    temp_path.replace(path)


def _atomic_write_json(path: Path, payload: Any, *, overwrite: bool = True) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True),
        overwrite=overwrite,
    )


def _append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_canonical_json(payload) + "\n")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ExperimentCorruptionError(f"required file is missing: {path}") from error
    except json.JSONDecodeError as error:
        raise ExperimentCorruptionError(f"invalid JSON in {path}: {error}") from error


def _coerce_dict(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ResearchRegistryError("payload must be an object")
    return payload


def _validate_experiment_id(experiment_id: str) -> str:
    normalized = experiment_id.strip().lower()
    if not _SAFE_EXPERIMENT_ID_RE.fullmatch(normalized):
        raise ResearchRegistryError(
            "experiment_id must match ^[a-z][a-z0-9_-]{2,63}$ "
            "after lowercase normalization"
        )
    return normalized


def _safe_seed_directory(seed: int) -> str:
    return f"seed-{seed}"


def _safe_run_id(experiment_id: str, seed: int, attempt: int) -> str:
    return f"{experiment_id}-seed-{seed}-attempt-{attempt}"


def _safe_relative_artifact_path(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/").strip("/")
    if (
        not normalized
        or normalized.startswith(".")
        or ".." in normalized.split("/")
        or not _SAFE_ARTIFACT_PATH_RE.fullmatch(normalized)
    ):
        raise ArtifactSanitizationError(f"unsafe artifact path '{relative_path}'")
    return normalized


def _sanitize_artifact_payload(payload: str) -> None:
    lowered = payload.lower()
    for term in _PROHIBITED_ARTIFACT_TEXT:
        if term in lowered:
            raise ArtifactSanitizationError(
                f"artifact payload contains prohibited material marker '{term}'"
            )


def _load_jsonl_records(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    records: list[dict[str, Any]] = []
    recovered_lines = 0
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                recovered_lines += 1
                continue
            if not isinstance(payload, dict):
                recovered_lines += 1
                continue
            checksum = str(payload.get("record_checksum", ""))
            canonical = dict(payload)
            canonical["record_checksum"] = ""
            if checksum != _sha256_text(_canonical_json(canonical)):
                recovered_lines += 1
                continue
            records.append(payload)
    return records, recovered_lines


def _record_checksum(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical["record_checksum"] = ""
    return _sha256_text(_canonical_json(canonical))


def _artifact_manifest_from_json(payload: dict[str, Any]) -> ArtifactManifest:
    entries = [
        ArtifactEntry(**entry) if not isinstance(entry, ArtifactEntry) else entry
        for entry in payload.get("entries", [])
    ]
    return ArtifactManifest(
        schema_version=int(
            payload.get("schema_version", ARTIFACT_MANIFEST_SCHEMA_VERSION)
        ),
        entries=entries,
        manifest_hash=str(payload.get("manifest_hash", "")),
    )


def _specification_from_payload(payload: dict[str, Any]) -> ExperimentSpecification:
    return ExperimentSpecification(
        schema_version=int(payload["schema_version"]),
        experiment_id=str(payload["experiment_id"]),
        experiment_name=str(payload["experiment_name"]),
        research_question=str(payload["research_question"]),
        dataset=DatasetConfiguration(**payload["dataset"]),
        partition=PartitionConfiguration(
            strategy=PartitionStrategy(payload["partition"]["strategy"]),
            num_clients=int(payload["partition"]["num_clients"]),
            seed=int(payload["partition"]["seed"]),
            minimum_client_samples=int(payload["partition"]["minimum_client_samples"]),
            alpha=payload["partition"].get("alpha"),
            classes_per_client=payload["partition"].get("classes_per_client"),
            quantity_skew_sigma=payload["partition"].get("quantity_skew_sigma"),
            partition_manifest_hash=str(
                payload["partition"]["partition_manifest_hash"]
            ),
        ),
        model=ModelConfiguration(**payload["model"]),
        algorithm=AlgorithmConfiguration(**payload["algorithm"]),
        privacy=PrivacyConfiguration(
            privacy_mode=PrivacyMode(payload["privacy"]["privacy_mode"]),
            noise_multiplier=payload["privacy"].get("noise_multiplier"),
            target_delta=payload["privacy"].get("target_delta"),
            user_level_clip_norm=payload["privacy"].get("user_level_clip_norm"),
            sample_level_max_grad_norm=payload["privacy"].get(
                "sample_level_max_grad_norm"
            ),
            epsilon_budget=payload["privacy"].get("epsilon_budget"),
            combined_epsilon=payload["privacy"].get("combined_epsilon"),
            client_weighting=str(payload["privacy"].get("client_weighting", "uniform")),
        ),
        secure_aggregation=SecureAggregationConfiguration(
            provider=SecureAggregationProvider(
                payload["secure_aggregation"]["provider"]
            ),
            dropout_recovery_requested=bool(
                payload["secure_aggregation"].get("dropout_recovery_requested", False)
            ),
        ),
        adaptive_clipping=AdaptiveClippingConfiguration(
            mode=AdaptiveClippingMode(payload["adaptive_clipping"]["mode"]),
            initial_bound=payload["adaptive_clipping"].get("initial_bound"),
            min_bound=payload["adaptive_clipping"].get("min_bound"),
            max_bound=payload["adaptive_clipping"].get("max_bound"),
            target_quantile=payload["adaptive_clipping"].get("target_quantile"),
            learning_rate=payload["adaptive_clipping"].get("learning_rate"),
            indicator_noise_multiplier=payload["adaptive_clipping"].get(
                "indicator_noise_multiplier"
            ),
        ),
        runtime=RuntimeLimits(**payload["runtime"]),
        seeds=SeedConfiguration(**payload["seeds"]),
        determinism_level=DeterminismLevel(payload["determinism_level"]),
        tags=[str(tag) for tag in payload.get("tags", [])],
        creation_timestamp=str(payload.get("creation_timestamp", "")),
        specification_hash=str(payload.get("specification_hash", "")),
    )


@dataclass(slots=True)
class StateTransitionRecord:
    transition_id: str
    previous_state: str
    new_state: str
    timestamp: str
    reason: str
    actor: str
    expected_version: int


@dataclass(slots=True)
class ArtifactEntry:
    artifact_id: str
    relative_path: str
    artifact_type: str
    schema_version: int
    mime_type: str
    byte_size: int
    sha256_checksum: str
    created_at: str
    producer: str
    sanitization_status: str
    retention_class: str
    public_safe: bool


@dataclass(slots=True)
class ArtifactManifest:
    schema_version: int = ARTIFACT_MANIFEST_SCHEMA_VERSION
    entries: list[ArtifactEntry] = field(default_factory=list)
    manifest_hash: str = ""

    def recompute_hash(self) -> str:
        payload = asdict(self)
        payload["manifest_hash"] = ""
        return _sha256_text(_canonical_json(payload))


@dataclass(slots=True)
class EnvironmentManifest:
    schema_version: int = ENVIRONMENT_MANIFEST_SCHEMA_VERSION
    generated_at: str = field(default_factory=_utc_now)
    operating_system: str = field(default_factory=platform.system)
    architecture: str = field(default_factory=platform.machine)
    cpu_summary: str = field(default_factory=lambda: platform.processor() or "unknown")
    gpu_summary: str = "not-detected"
    python_version: str = field(default_factory=platform.python_version)
    numpy_version: str = ""
    pytorch_version: str = ""
    opacus_version: str = ""
    go_version: str = ""
    node_version: str = ""
    dependency_lockfile_hashes: dict[str, str] = field(default_factory=dict)
    thread_settings: dict[str, str] = field(default_factory=dict)
    determinism_policy: str = ""
    secure_aggregation_provider: str = ""
    git_revision: str = ""
    dirty_working_tree: bool = False
    sanitized_diff_summary_hash: str = ""
    manifest_hash: str = ""

    def recompute_hash(self) -> str:
        payload = asdict(self)
        payload["manifest_hash"] = ""
        return _sha256_text(_canonical_json(payload))


@dataclass(slots=True)
class ExperimentRegistryRecord:
    schema_version: int
    experiment_id: str
    display_name: str
    research_question: str
    specification_hash: str
    dataset_id: str
    dataset_version: str
    dataset_checksum: str
    partition_manifest_hash: str
    model_id: str
    algorithm_id: str
    privacy_mode: str
    secure_aggregation_enabled: bool
    secure_aggregation_provider: str
    adaptive_clipping_enabled: bool
    declared_seed_count: int
    current_state: str
    successful_run_count: int
    failed_run_count: int
    canceled_run_count: int
    blocked_run_count: int
    created_at: str
    updated_at: str
    created_actor: str
    record_version: int
    storage_format_version: int
    artifact_manifest_hash: str
    environment_manifest_hash: str
    degraded: bool
    degraded_reason: str = ""


@dataclass(slots=True)
class AttemptHistoryRecord:
    attempt: int
    state: str
    started_at: str = ""
    completed_at: str = ""
    failure_reason: str = ""


@dataclass(slots=True)
class ExperimentRunRecord:
    schema_version: int
    experiment_id: str
    specification_hash: str
    seed: int
    run_id: str
    run_attempt: int
    current_state: str
    partition_manifest_hash: str
    model_initialization_seed: int
    training_seed: int
    worker_assignment_seed: int
    start_timestamp: str = ""
    completion_timestamp: str = ""
    last_heartbeat: str = ""
    current_round: int = 0
    expected_round_count: int = 0
    model_version: str = ""
    environment_manifest_hash: str = ""
    metric_schema_version: int = METRIC_RECORD_SCHEMA_VERSION
    result_summary_hash: str = ""
    failure_count: int = 0
    retry_lineage: list[int] = field(default_factory=list)
    inclusion_status: str = InclusionStatus.INCLUDED.value
    exclusion_reason: str = ""
    artifact_manifest_hash: str = ""
    record_version: int = 1
    attempt_history: list[AttemptHistoryRecord] = field(default_factory=list)


@dataclass(slots=True)
class SyntheticExecutionResult:
    completed: bool
    summary: dict[str, Any] = field(default_factory=dict)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    events: list[tuple[str, str]] = field(default_factory=list)
    artifact_payloads: dict[str, str] = field(default_factory=dict)
    failure_reason: str = ""
    blocked: bool = False
    canceled: bool = False


class SyntheticExecutionAdapter(Protocol):
    def execute(
        self,
        specification: ExperimentSpecification,
        run_record: ExperimentRunRecord,
    ) -> SyntheticExecutionResult: ...


_ALLOWED_EXPERIMENT_TRANSITIONS = {
    ExperimentState.CREATED: {ExperimentState.VALIDATED, ExperimentState.FAILED},
    ExperimentState.VALIDATED: {ExperimentState.PREPARING, ExperimentState.FAILED},
    ExperimentState.PREPARING: {
        ExperimentState.READY,
        ExperimentState.CANCEL_REQUESTED,
        ExperimentState.FAILED,
        ExperimentState.CORRUPTED,
    },
    ExperimentState.READY: {
        ExperimentState.RUNNING,
        ExperimentState.CANCEL_REQUESTED,
        ExperimentState.FAILED,
    },
    ExperimentState.RUNNING: {
        ExperimentState.CANCEL_REQUESTED,
        ExperimentState.COMPLETED,
        ExperimentState.COMPLETED_WITH_PARTIAL_RUNS,
        ExperimentState.FAILED,
        ExperimentState.BLOCKED,
    },
    ExperimentState.CANCEL_REQUESTED: {
        ExperimentState.CANCELED,
        ExperimentState.COMPLETED_WITH_PARTIAL_RUNS,
        ExperimentState.FAILED,
    },
    ExperimentState.CANCELED: set(),
    ExperimentState.COMPLETED: set(),
    ExperimentState.COMPLETED_WITH_PARTIAL_RUNS: set(),
    ExperimentState.FAILED: set(),
    ExperimentState.BLOCKED: set(),
    ExperimentState.CORRUPTED: set(),
}

_ALLOWED_RUN_TRANSITIONS = {
    RunState.CREATED: {RunState.PREPARING, RunState.CANCELED, RunState.CORRUPTED},
    RunState.PREPARING: {RunState.RUNNING, RunState.FAILED, RunState.CANCELED},
    RunState.RUNNING: {
        RunState.EVALUATING,
        RunState.FAILED,
        RunState.CANCELED,
        RunState.LOST,
    },
    RunState.EVALUATING: {
        RunState.COMPLETED,
        RunState.FAILED,
        RunState.CANCELED,
        RunState.BLOCKED,
    },
    RunState.COMPLETED: set(),
    RunState.FAILED: {RunState.CREATED},
    RunState.CANCELED: set(),
    RunState.BLOCKED: {RunState.CREATED},
    RunState.LOST: {RunState.CREATED},
    RunState.CORRUPTED: set(),
}


class ExperimentRegistry:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.experiments_root = self.root / "experiments"
        self.idempotency_root = self.root / "idempotency"
        self._lock = threading.Lock()

    def experiment_directory(self, experiment_id: str) -> Path:
        return self.experiments_root / _validate_experiment_id(experiment_id)

    def create_experiment(
        self,
        specification: ExperimentSpecification,
        *,
        actor: str,
        idempotency_key: str,
        environment_manifest: EnvironmentManifest | None = None,
    ) -> ExperimentRegistryRecord:
        validated = validate_experiment_specification(specification)
        experiment_id = _validate_experiment_id(validated.experiment_id)
        unique_seeds = list(dict.fromkeys(validated.seeds.seeds))
        if len(unique_seeds) != len(validated.seeds.seeds):
            raise ResearchRegistryError("seed list contains duplicates")
        expected_hash = validated.compute_hash()
        if (
            validated.specification_hash
            and validated.specification_hash != expected_hash
        ):
            raise ResearchRegistryError("specification hash mismatch during create")
        validated.specification_hash = expected_hash
        env_manifest = environment_manifest or build_environment_manifest(validated)
        env_manifest.manifest_hash = env_manifest.recompute_hash()
        request_hash = _sha256_text(
            _canonical_json(
                {
                    "experiment_id": experiment_id,
                    "idempotency_key": idempotency_key,
                    "specification_hash": expected_hash,
                }
            )
        )
        idempotency_path = self.idempotency_root / f"{idempotency_key}.json"
        with self._lock:
            if idempotency_key and idempotency_path.exists():
                cached = _read_json(idempotency_path)
                if cached["request_hash"] != request_hash:
                    raise ExperimentConflictError(
                        "idempotency key reuse conflicts with a different request body"
                    )
                return self.get_registry_record(str(cached["experiment_id"]))
            experiment_dir = self.experiment_directory(experiment_id)
            if experiment_dir.exists():
                raise ExperimentConflictError(
                    f"experiment '{experiment_id}' already exists"
                )
            spec_payload = validated.canonical_payload()
            spec_payload["specification_hash"] = validated.specification_hash
            spec_text = json.dumps(
                spec_payload, indent=2, sort_keys=True, ensure_ascii=True
            )
            spec_sha = _sha256_text(spec_text)
            artifact_manifest = ArtifactManifest()
            self._write_registered_artifact(
                artifact_manifest,
                experiment_dir,
                relative_path="specification.json",
                payload=spec_text,
                artifact_type="specification",
                mime_type="application/json",
                producer="registry.create",
                public_safe=True,
            )
            _atomic_write_text(
                experiment_dir / "specification.sha256",
                spec_sha + "\n",
                overwrite=False,
            )
            env_text = json.dumps(
                asdict(env_manifest), indent=2, sort_keys=True, ensure_ascii=True
            )
            self._write_registered_artifact(
                artifact_manifest,
                experiment_dir,
                relative_path="environment.json",
                payload=env_text,
                artifact_type="environment_manifest",
                mime_type="application/json",
                producer="registry.create",
                public_safe=True,
            )
            artifact_manifest.manifest_hash = artifact_manifest.recompute_hash()
            registry_record = ExperimentRegistryRecord(
                schema_version=EXPERIMENT_REGISTRY_SCHEMA_VERSION,
                experiment_id=experiment_id,
                display_name=validated.experiment_name,
                research_question=validated.research_question,
                specification_hash=validated.specification_hash,
                dataset_id=validated.dataset.dataset_id,
                dataset_version=validated.dataset.dataset_version,
                dataset_checksum=validated.dataset.dataset_checksum,
                partition_manifest_hash=validated.partition.partition_manifest_hash,
                model_id=validated.model.model_id,
                algorithm_id=validated.algorithm.algorithm_id,
                privacy_mode=validated.privacy.privacy_mode.value,
                secure_aggregation_enabled=(
                    validated.secure_aggregation.provider.value != "none"
                ),
                secure_aggregation_provider=validated.secure_aggregation.provider.value,
                adaptive_clipping_enabled=(
                    validated.adaptive_clipping.mode.value != "disabled"
                ),
                declared_seed_count=len(unique_seeds),
                current_state=ExperimentState.READY.value,
                successful_run_count=0,
                failed_run_count=0,
                canceled_run_count=0,
                blocked_run_count=0,
                created_at=_utc_now(),
                updated_at=_utc_now(),
                created_actor=actor,
                record_version=1,
                storage_format_version=1,
                artifact_manifest_hash=artifact_manifest.manifest_hash,
                environment_manifest_hash=env_manifest.manifest_hash,
                degraded=False,
            )
            _atomic_write_json(
                experiment_dir / "registry.json",
                asdict(registry_record),
                overwrite=False,
            )
            _atomic_write_json(
                experiment_dir / "state.json",
                {
                    "schema_version": 1,
                    "experiment_id": experiment_id,
                    "current_state": ExperimentState.READY.value,
                    "record_version": 1,
                    "transitions": [
                        asdict(
                            StateTransitionRecord(
                                transition_id="transition-created",
                                previous_state="",
                                new_state=ExperimentState.CREATED.value,
                                timestamp=registry_record.created_at,
                                reason="experiment created",
                                actor=actor,
                                expected_version=0,
                            )
                        ),
                        asdict(
                            StateTransitionRecord(
                                transition_id="transition-ready",
                                previous_state=ExperimentState.CREATED.value,
                                new_state=ExperimentState.READY.value,
                                timestamp=registry_record.updated_at,
                                reason="validated and prepared",
                                actor=actor,
                                expected_version=1,
                            )
                        ),
                    ],
                },
                overwrite=False,
            )
            _atomic_write_json(
                experiment_dir / "compatibility.json",
                {
                    "schema_version": 1,
                    "specification_hash": validated.specification_hash,
                    "privacy_mode": validated.privacy.privacy_mode.value,
                    "secure_aggregation_provider": (
                        validated.secure_aggregation.provider.value
                    ),
                    "dropout_recovery_allowed": False,
                    "combined_epsilon_allowed": False,
                    "validated_at": _utc_now(),
                },
                overwrite=False,
            )
            _atomic_write_json(
                experiment_dir / "artifacts.json",
                asdict(artifact_manifest),
                overwrite=False,
            )
            _atomic_write_text(experiment_dir / "events.jsonl", "", overwrite=False)
            for seed in unique_seeds:
                self._initialize_run(experiment_dir, validated, seed, env_manifest)
            if idempotency_key:
                _atomic_write_json(
                    idempotency_path,
                    {
                        "schema_version": 1,
                        "idempotency_key": idempotency_key,
                        "experiment_id": experiment_id,
                        "request_hash": request_hash,
                        "created_at": _utc_now(),
                    },
                    overwrite=False,
                )
            self.append_event(
                experiment_id,
                event_type="EXPERIMENT_CREATED",
                actor=actor,
                reason="immutable experiment snapshot persisted",
            )
            return registry_record

    def _initialize_run(
        self,
        experiment_dir: Path,
        specification: ExperimentSpecification,
        seed: int,
        environment_manifest: EnvironmentManifest,
    ) -> None:
        run_dir = experiment_dir / "runs" / _safe_seed_directory(seed)
        run_record = ExperimentRunRecord(
            schema_version=RUN_RECORD_SCHEMA_VERSION,
            experiment_id=specification.experiment_id,
            specification_hash=specification.specification_hash,
            seed=seed,
            run_id=_safe_run_id(specification.experiment_id, seed, 1),
            run_attempt=1,
            current_state=RunState.CREATED.value,
            partition_manifest_hash=specification.partition.partition_manifest_hash,
            model_initialization_seed=specification.model.initialization_seed,
            training_seed=seed,
            worker_assignment_seed=specification.seeds.worker_assignment_seed,
            expected_round_count=specification.runtime.max_rounds,
            model_version=specification.model.model_version,
            environment_manifest_hash=environment_manifest.manifest_hash,
            attempt_history=[
                AttemptHistoryRecord(attempt=1, state=RunState.CREATED.value)
            ],
        )
        _atomic_write_json(
            run_dir / "run.json", _run_record_to_json(run_record), overwrite=False
        )
        _atomic_write_json(
            run_dir / "state.json",
            {
                "schema_version": 1,
                "experiment_id": specification.experiment_id,
                "seed": seed,
                "current_state": RunState.CREATED.value,
                "record_version": 1,
            },
            overwrite=False,
        )
        _atomic_write_json(
            run_dir / "environment.json",
            {
                "environment_manifest_hash": environment_manifest.manifest_hash,
                "inherited_from_experiment": True,
            },
            overwrite=False,
        )
        _atomic_write_json(
            run_dir / "artifacts.json",
            {"schema_version": 1, "entries": [], "manifest_hash": ""},
            overwrite=False,
        )
        _atomic_write_text(run_dir / "metrics.jsonl", "", overwrite=False)
        _atomic_write_text(run_dir / "failures.jsonl", "", overwrite=False)

    def _write_registered_artifact(
        self,
        manifest: ArtifactManifest,
        experiment_dir: Path,
        *,
        relative_path: str,
        payload: str,
        artifact_type: str,
        mime_type: str,
        producer: str,
        public_safe: bool,
    ) -> None:
        safe_path = _safe_relative_artifact_path(relative_path)
        _sanitize_artifact_payload(payload)
        target_path = experiment_dir / safe_path
        _atomic_write_text(target_path, payload, overwrite=False)
        manifest.entries.append(
            ArtifactEntry(
                artifact_id=f"artifact-{len(manifest.entries) + 1}",
                relative_path=safe_path,
                artifact_type=artifact_type,
                schema_version=1,
                mime_type=mime_type,
                byte_size=len(payload.encode("utf-8")),
                sha256_checksum=_sha256_text(payload),
                created_at=_utc_now(),
                producer=producer,
                sanitization_status="passed",
                retention_class="research_registry",
                public_safe=public_safe,
            )
        )

    def list_experiments(self) -> list[ExperimentRegistryRecord]:
        items: list[ExperimentRegistryRecord] = []
        if not self.experiments_root.exists():
            return items
        for experiment_dir in sorted(self.experiments_root.iterdir()):
            if not experiment_dir.is_dir():
                continue
            items.append(self.get_registry_record(experiment_dir.name))
        return items

    def get_registry_record(self, experiment_id: str) -> ExperimentRegistryRecord:
        payload = _read_json(self.experiment_directory(experiment_id) / "registry.json")
        return ExperimentRegistryRecord(**payload)

    def get_specification_payload(self, experiment_id: str) -> dict[str, Any]:
        experiment_dir = self.experiment_directory(experiment_id)
        spec_text = (experiment_dir / "specification.json").read_text(encoding="utf-8")
        stored_checksum = (
            (experiment_dir / "specification.sha256")
            .read_text(encoding="utf-8")
            .strip()
        )
        actual_checksum = _sha256_text(spec_text)
        if stored_checksum != actual_checksum:
            raise ExperimentCorruptionError(
                f"specification checksum mismatch for experiment '{experiment_id}'"
            )
        payload = _coerce_dict(json.loads(spec_text))
        expected_hash = str(payload["specification_hash"])
        canonical = dict(payload)
        canonical["specification_hash"] = ""
        actual_hash = _sha256_text(_canonical_json(canonical))
        if expected_hash != actual_hash:
            raise ExperimentCorruptionError(
                f"specification hash mismatch for experiment '{experiment_id}'"
            )
        return payload

    def transition_experiment_state(
        self,
        experiment_id: str,
        next_state: ExperimentState,
        *,
        actor: str,
        reason: str,
        expected_version: int,
    ) -> ExperimentRegistryRecord:
        with self._lock:
            experiment_dir = self.experiment_directory(experiment_id)
            registry_payload = _read_json(experiment_dir / "registry.json")
            if int(registry_payload["record_version"]) != expected_version:
                raise ExperimentConflictError("stale experiment record version")
            current_state = ExperimentState(str(registry_payload["current_state"]))
            if next_state not in _ALLOWED_EXPERIMENT_TRANSITIONS[current_state]:
                raise ResearchRegistryError(
                    "invalid experiment transition "
                    f"{current_state.value} -> {next_state.value}"
                )
            registry_payload["current_state"] = next_state.value
            registry_payload["record_version"] = expected_version + 1
            registry_payload["updated_at"] = _utc_now()
            _atomic_write_json(experiment_dir / "registry.json", registry_payload)
            state_payload = _read_json(experiment_dir / "state.json")
            state_payload["current_state"] = next_state.value
            state_payload["record_version"] = int(state_payload["record_version"]) + 1
            state_payload["transitions"].append(
                asdict(
                    StateTransitionRecord(
                        transition_id=f"transition-{int(time.time() * 1_000_000)}",
                        previous_state=current_state.value,
                        new_state=next_state.value,
                        timestamp=registry_payload["updated_at"],
                        reason=reason,
                        actor=actor,
                        expected_version=expected_version,
                    )
                )
            )
            _atomic_write_json(experiment_dir / "state.json", state_payload)
            self.append_event(
                experiment_id,
                event_type=f"EXPERIMENT_{next_state.value}",
                actor=actor,
                reason=reason,
            )
            return ExperimentRegistryRecord(**registry_payload)

    def get_run_record(self, experiment_id: str, seed: int) -> ExperimentRunRecord:
        payload = _read_json(
            self.experiment_directory(experiment_id)
            / "runs"
            / _safe_seed_directory(seed)
            / "run.json"
        )
        return _run_record_from_json(payload)

    def transition_run_state(
        self,
        experiment_id: str,
        seed: int,
        next_state: RunState,
        *,
        reason: str,
    ) -> ExperimentRunRecord:
        with self._lock:
            run_path = (
                self.experiment_directory(experiment_id)
                / "runs"
                / _safe_seed_directory(seed)
                / "run.json"
            )
            payload = _read_json(run_path)
            current_state = RunState(str(payload["current_state"]))
            if next_state not in _ALLOWED_RUN_TRANSITIONS[current_state]:
                raise ResearchRegistryError(
                    "invalid run transition "
                    f"{current_state.value} -> {next_state.value}"
                )
            payload["current_state"] = next_state.value
            payload["record_version"] = int(payload["record_version"]) + 1
            payload["last_heartbeat"] = _utc_now()
            if (
                next_state in {RunState.PREPARING, RunState.RUNNING}
                and not payload["start_timestamp"]
            ):
                payload["start_timestamp"] = payload["last_heartbeat"]
            if next_state in {
                RunState.COMPLETED,
                RunState.FAILED,
                RunState.CANCELED,
                RunState.BLOCKED,
                RunState.LOST,
                RunState.CORRUPTED,
            }:
                payload["completion_timestamp"] = payload["last_heartbeat"]
            payload["attempt_history"][-1]["state"] = next_state.value
            if next_state in {RunState.FAILED, RunState.BLOCKED, RunState.LOST}:
                payload["failure_count"] = int(payload["failure_count"]) + 1
                _append_jsonl(
                    run_path.parent / "failures.jsonl",
                    self._event_payload(
                        experiment_id=experiment_id,
                        run_id=str(payload["run_id"]),
                        seed=seed,
                        event_type=f"RUN_{next_state.value}",
                        reason=reason,
                        actor="system",
                    ),
                )
            _atomic_write_json(run_path, payload)
            _atomic_write_json(
                run_path.parent / "state.json",
                {
                    "schema_version": 1,
                    "experiment_id": experiment_id,
                    "seed": seed,
                    "current_state": next_state.value,
                    "record_version": payload["record_version"],
                },
            )
            return _run_record_from_json(payload)

    def create_retry_attempt(
        self, experiment_id: str, seed: int, *, actor: str, reason: str
    ) -> ExperimentRunRecord:
        with self._lock:
            run_path = (
                self.experiment_directory(experiment_id)
                / "runs"
                / _safe_seed_directory(seed)
                / "run.json"
            )
            payload = _read_json(run_path)
            current_state = RunState(str(payload["current_state"]))
            if current_state not in {RunState.FAILED, RunState.BLOCKED, RunState.LOST}:
                raise ResearchRegistryError(
                    "retry requires the latest attempt to be FAILED, BLOCKED, or LOST"
                )
            next_attempt = int(payload["run_attempt"]) + 1
            payload["retry_lineage"].append(int(payload["run_attempt"]))
            payload["run_attempt"] = next_attempt
            payload["run_id"] = _safe_run_id(experiment_id, seed, next_attempt)
            payload["current_state"] = RunState.CREATED.value
            payload["record_version"] = int(payload["record_version"]) + 1
            payload["start_timestamp"] = ""
            payload["completion_timestamp"] = ""
            payload["last_heartbeat"] = ""
            payload["current_round"] = 0
            payload["result_summary_hash"] = ""
            payload["attempt_history"].append(
                asdict(
                    AttemptHistoryRecord(
                        attempt=next_attempt, state=RunState.CREATED.value
                    )
                )
            )
            _atomic_write_json(run_path, payload)
            self.append_event(
                experiment_id,
                event_type="RUN_RETRY_CREATED",
                actor=actor,
                reason=reason,
                run_id=str(payload["run_id"]),
                seed=seed,
            )
            return _run_record_from_json(payload)

    def append_event(
        self,
        experiment_id: str,
        *,
        event_type: str,
        actor: str,
        reason: str,
        run_id: str = "",
        seed: int | None = None,
    ) -> None:
        payload = self._event_payload(
            experiment_id=experiment_id,
            event_type=event_type,
            reason=reason,
            actor=actor,
            run_id=run_id,
            seed=seed,
        )
        _append_jsonl(
            self.experiment_directory(experiment_id) / "events.jsonl", payload
        )

    def _event_payload(
        self,
        *,
        experiment_id: str,
        event_type: str,
        reason: str,
        actor: str,
        run_id: str = "",
        seed: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": EVENT_RECORD_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "run_id": run_id,
            "seed": seed,
            "sequence": int(time.time() * 1_000_000),
            "timestamp": _utc_now(),
            "event_type": event_type,
            "actor": actor,
            "reason": reason,
            "record_checksum": "",
        }
        payload["record_checksum"] = _record_checksum(payload)
        return payload

    def append_metric(
        self,
        experiment_id: str,
        seed: int,
        *,
        run_id: str,
        scope: str,
        metric_name: str,
        value: float,
        unit: str,
        round_index: int,
        model_version: str,
        source_component: str,
        tags: list[str] | None = None,
    ) -> None:
        if scope not in _ALLOWED_METRIC_SCOPES:
            raise ResearchRegistryError(f"unsupported metric scope '{scope}'")
        if metric_name not in _ALLOWED_METRIC_NAMES:
            raise ResearchRegistryError(f"unsupported metric name '{metric_name}'")
        payload = {
            "schema_version": METRIC_RECORD_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "run_id": run_id,
            "seed": seed,
            "metric_scope": scope,
            "metric_name": metric_name,
            "numeric_value": value,
            "unit": unit,
            "round": round_index,
            "model_version": model_version,
            "timestamp": _utc_now(),
            "source_component": source_component,
            "tags": list(tags or []),
            "record_checksum": "",
        }
        payload["record_checksum"] = _record_checksum(payload)
        _append_jsonl(
            self.experiment_directory(experiment_id)
            / "runs"
            / _safe_seed_directory(seed)
            / "metrics.jsonl",
            payload,
        )

    def list_metrics(
        self, experiment_id: str, seed: int
    ) -> tuple[list[dict[str, Any]], int]:
        return _load_jsonl_records(
            self.experiment_directory(experiment_id)
            / "runs"
            / _safe_seed_directory(seed)
            / "metrics.jsonl"
        )

    def list_events(self, experiment_id: str) -> tuple[list[dict[str, Any]], int]:
        return _load_jsonl_records(
            self.experiment_directory(experiment_id) / "events.jsonl"
        )

    def request_cancel(
        self, experiment_id: str, *, actor: str, expected_version: int
    ) -> ExperimentRegistryRecord:
        record = self.transition_experiment_state(
            experiment_id,
            ExperimentState.CANCEL_REQUESTED,
            actor=actor,
            reason="cancellation requested",
            expected_version=expected_version,
        )
        for run_record in self.list_run_records(experiment_id):
            if RunState(run_record.current_state) == RunState.CREATED:
                self.transition_run_state(
                    experiment_id,
                    run_record.seed,
                    RunState.CANCELED,
                    reason="experiment cancellation prevented unstarted seed execution",
                )
        return record

    def list_run_records(self, experiment_id: str) -> list[ExperimentRunRecord]:
        runs_root = self.experiment_directory(experiment_id) / "runs"
        records: list[ExperimentRunRecord] = []
        for seed_dir in sorted(runs_root.iterdir()):
            if not seed_dir.is_dir():
                continue
            records.append(_run_record_from_json(_read_json(seed_dir / "run.json")))
        return records

    def detect_and_mark_corruption(self, experiment_id: str) -> None:
        experiment_dir = self.experiment_directory(experiment_id)
        self.get_specification_payload(experiment_id)
        artifact_manifest = _artifact_manifest_from_json(
            _read_json(experiment_dir / "artifacts.json")
        )
        for entry in artifact_manifest.entries:
            artifact_path = experiment_dir / _safe_relative_artifact_path(
                entry.relative_path
            )
            actual = _sha256_bytes(artifact_path.read_bytes())
            if actual != entry.sha256_checksum:
                raise ExperimentCorruptionError(
                    f"artifact checksum mismatch for '{entry.relative_path}'"
                )

    def recover(self) -> dict[str, int]:
        stale_runs = 0
        corrupted_experiments = 0
        for record in self.list_experiments():
            for run_record in self.list_run_records(record.experiment_id):
                if run_record.current_state in {
                    RunState.PREPARING.value,
                    RunState.RUNNING.value,
                    RunState.EVALUATING.value,
                }:
                    self.transition_run_state(
                        record.experiment_id,
                        run_record.seed,
                        RunState.LOST,
                        reason=(
                            "registry recovery marked in-flight run lost after restart"
                        ),
                    )
                    stale_runs += 1
            try:
                self.detect_and_mark_corruption(record.experiment_id)
            except ExperimentCorruptionError:
                corrupted_experiments += 1
                with suppress(ResearchRegistryError):
                    self.transition_experiment_state(
                        record.experiment_id,
                        ExperimentState.CORRUPTED,
                        actor="system",
                        reason="corruption detected during recovery scan",
                        expected_version=record.record_version,
                    )
                continue
        return {
            "stale_run_count": stale_runs,
            "corrupted_experiment_count": corrupted_experiments,
        }


class BoundedExperimentOrchestrator:
    def __init__(
        self, registry: ExperimentRegistry, adapter: SyntheticExecutionAdapter
    ) -> None:
        self.registry = registry
        self.adapter = adapter

    def execute_experiment(self, experiment_id: str) -> ExperimentRegistryRecord:
        record = self.registry.get_registry_record(experiment_id)
        if ExperimentState(record.current_state) not in {
            ExperimentState.READY,
            ExperimentState.RUNNING,
            ExperimentState.CANCEL_REQUESTED,
        }:
            raise ResearchRegistryError(
                "experiment "
                f"'{experiment_id}' is not runnable from state "
                f"{record.current_state}"
            )
        if ExperimentState(record.current_state) == ExperimentState.READY:
            record = self.registry.transition_experiment_state(
                experiment_id,
                ExperimentState.RUNNING,
                actor="system",
                reason="bounded orchestration started",
                expected_version=record.record_version,
            )
        specification_payload = self.registry.get_specification_payload(experiment_id)
        specification = _specification_from_payload(specification_payload)
        for run_record in self.registry.list_run_records(experiment_id):
            experiment_state = ExperimentState(
                self.registry.get_registry_record(experiment_id).current_state
            )
            if (
                experiment_state == ExperimentState.CANCEL_REQUESTED
                and RunState(run_record.current_state) == RunState.CREATED
            ):
                continue
            if RunState(run_record.current_state) not in {RunState.CREATED}:
                continue
            self.registry.transition_run_state(
                experiment_id,
                run_record.seed,
                RunState.PREPARING,
                reason="preparing synthetic run execution",
            )
            run_record = self.registry.transition_run_state(
                experiment_id,
                run_record.seed,
                RunState.RUNNING,
                reason="synthetic run execution started",
            )
            self.registry.append_event(
                experiment_id,
                event_type="RUN_STARTED",
                actor="system",
                reason="synthetic execution adapter started",
                run_id=run_record.run_id,
                seed=run_record.seed,
            )
            result = self.adapter.execute(specification, run_record)
            if result.canceled:
                self.registry.transition_run_state(
                    experiment_id,
                    run_record.seed,
                    RunState.CANCELED,
                    reason=result.failure_reason
                    or "synthetic adapter reported cancellation",
                )
                continue
            if result.blocked:
                self.registry.transition_run_state(
                    experiment_id,
                    run_record.seed,
                    RunState.BLOCKED,
                    reason=result.failure_reason or "synthetic adapter blocked",
                )
                continue
            if not result.completed:
                self.registry.transition_run_state(
                    experiment_id,
                    run_record.seed,
                    RunState.FAILED,
                    reason=result.failure_reason
                    or "synthetic adapter reported failure",
                )
                continue
            self.registry.transition_run_state(
                experiment_id,
                run_record.seed,
                RunState.EVALUATING,
                reason="recording synthetic metrics and summary",
            )
            for metric in result.metrics:
                self.registry.append_metric(
                    experiment_id,
                    run_record.seed,
                    run_id=run_record.run_id,
                    scope=str(metric["scope"]),
                    metric_name=str(metric["name"]),
                    value=float(metric["value"]),
                    unit=str(metric.get("unit", "")),
                    round_index=int(metric.get("round", 0)),
                    model_version=str(
                        metric.get("model_version", run_record.model_version)
                    ),
                    source_component=str(
                        metric.get("source_component", "synthetic-adapter")
                    ),
                    tags=[str(tag) for tag in metric.get("tags", [])],
                )
            summary_text = json.dumps(
                result.summary, indent=2, sort_keys=True, ensure_ascii=True
            )
            self.registry._write_registered_artifact(
                ArtifactManifest(),
                self.registry.experiment_directory(experiment_id)
                / "runs"
                / _safe_seed_directory(run_record.seed),
                relative_path="summary.json",
                payload=summary_text,
                artifact_type="run_summary",
                mime_type="application/json",
                producer="synthetic-adapter",
                public_safe=True,
            )
            for relative_path, payload in result.artifact_payloads.items():
                self.registry._write_registered_artifact(
                    ArtifactManifest(),
                    self.registry.experiment_directory(experiment_id)
                    / "runs"
                    / _safe_seed_directory(run_record.seed),
                    relative_path=relative_path,
                    payload=payload,
                    artifact_type="sanitized_log",
                    mime_type="text/plain",
                    producer="synthetic-adapter",
                    public_safe=True,
                )
            self.registry.transition_run_state(
                experiment_id,
                run_record.seed,
                RunState.COMPLETED,
                reason="synthetic adapter completed successfully",
            )
            self.registry.append_event(
                experiment_id,
                event_type="RUN_COMPLETED",
                actor="system",
                reason="synthetic execution completed",
                run_id=run_record.run_id,
                seed=run_record.seed,
            )
        return self._reconcile_experiment_state(experiment_id)

    def _reconcile_experiment_state(
        self, experiment_id: str
    ) -> ExperimentRegistryRecord:
        record = self.registry.get_registry_record(experiment_id)
        runs = self.registry.list_run_records(experiment_id)
        completed = sum(
            1 for run in runs if run.current_state == RunState.COMPLETED.value
        )
        failed = sum(1 for run in runs if run.current_state == RunState.FAILED.value)
        canceled = sum(
            1 for run in runs if run.current_state == RunState.CANCELED.value
        )
        blocked = sum(1 for run in runs if run.current_state == RunState.BLOCKED.value)
        registry_payload = asdict(record)
        registry_payload["successful_run_count"] = completed
        registry_payload["failed_run_count"] = failed
        registry_payload["canceled_run_count"] = canceled
        registry_payload["blocked_run_count"] = blocked
        registry_payload["record_version"] = record.record_version + 1
        registry_payload["updated_at"] = _utc_now()
        if completed == len(runs):
            next_state = ExperimentState.COMPLETED
        elif completed > 0 and failed + canceled + blocked > 0:
            next_state = ExperimentState.COMPLETED_WITH_PARTIAL_RUNS
        elif failed == len(runs):
            next_state = ExperimentState.FAILED
        elif blocked > 0 and completed == 0:
            next_state = ExperimentState.BLOCKED
        elif canceled == len(runs) or (
            ExperimentState(record.current_state) == ExperimentState.CANCEL_REQUESTED
        ):
            next_state = ExperimentState.CANCELED
        else:
            next_state = ExperimentState.RUNNING
        registry_payload["current_state"] = next_state.value
        _atomic_write_json(
            self.registry.experiment_directory(experiment_id) / "registry.json",
            registry_payload,
        )
        return ExperimentRegistryRecord(**registry_payload)


def _run_record_to_json(run_record: ExperimentRunRecord) -> dict[str, Any]:
    payload = asdict(run_record)
    payload["attempt_history"] = [asdict(item) for item in run_record.attempt_history]
    return payload


def _run_record_from_json(payload: dict[str, Any]) -> ExperimentRunRecord:
    history = [
        AttemptHistoryRecord(**item)
        if not isinstance(item, AttemptHistoryRecord)
        else item
        for item in payload.get("attempt_history", [])
    ]
    return ExperimentRunRecord(
        schema_version=int(payload["schema_version"]),
        experiment_id=str(payload["experiment_id"]),
        specification_hash=str(payload["specification_hash"]),
        seed=int(payload["seed"]),
        run_id=str(payload["run_id"]),
        run_attempt=int(payload["run_attempt"]),
        current_state=str(payload["current_state"]),
        partition_manifest_hash=str(payload["partition_manifest_hash"]),
        model_initialization_seed=int(payload["model_initialization_seed"]),
        training_seed=int(payload["training_seed"]),
        worker_assignment_seed=int(payload["worker_assignment_seed"]),
        start_timestamp=str(payload.get("start_timestamp", "")),
        completion_timestamp=str(payload.get("completion_timestamp", "")),
        last_heartbeat=str(payload.get("last_heartbeat", "")),
        current_round=int(payload.get("current_round", 0)),
        expected_round_count=int(payload.get("expected_round_count", 0)),
        model_version=str(payload.get("model_version", "")),
        environment_manifest_hash=str(payload.get("environment_manifest_hash", "")),
        metric_schema_version=int(
            payload.get("metric_schema_version", METRIC_RECORD_SCHEMA_VERSION)
        ),
        result_summary_hash=str(payload.get("result_summary_hash", "")),
        failure_count=int(payload.get("failure_count", 0)),
        retry_lineage=[int(value) for value in payload.get("retry_lineage", [])],
        inclusion_status=str(
            payload.get("inclusion_status", InclusionStatus.INCLUDED.value)
        ),
        exclusion_reason=str(payload.get("exclusion_reason", "")),
        artifact_manifest_hash=str(payload.get("artifact_manifest_hash", "")),
        record_version=int(payload.get("record_version", 1)),
        attempt_history=history,
    )


def build_environment_manifest(
    specification: ExperimentSpecification,
    *,
    git_revision: str = "",
    dirty_working_tree: bool = False,
    dependency_lockfile_hashes: dict[str, str] | None = None,
) -> EnvironmentManifest:
    numpy_version = ""
    pytorch_version = ""
    opacus_version = ""
    try:
        import numpy as np

        numpy_version = str(np.__version__)
    except Exception:
        numpy_version = ""
    try:
        import torch

        pytorch_version = str(torch.__version__)
    except Exception:
        pytorch_version = ""
    try:
        opacus_version = importlib.metadata.version("opacus")
    except importlib.metadata.PackageNotFoundError:
        opacus_version = ""
    manifest = EnvironmentManifest(
        numpy_version=numpy_version,
        pytorch_version=pytorch_version,
        opacus_version=opacus_version,
        dependency_lockfile_hashes=dict(dependency_lockfile_hashes or {}),
        determinism_policy=specification.determinism_level.value,
        secure_aggregation_provider=specification.secure_aggregation.provider.value,
        git_revision=git_revision,
        dirty_working_tree=dirty_working_tree,
        sanitized_diff_summary_hash=(
            _sha256_text(f"{git_revision}:{dirty_working_tree}")
            if dirty_working_tree
            else ""
        ),
        thread_settings={"python_hash_seed": os.getenv("PYTHONHASHSEED", "")},
    )
    manifest.manifest_hash = manifest.recompute_hash()
    return manifest
