from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .identity import TrackingIdentityBundle


@dataclass(slots=True)
class MetricPoint:
    key: str
    value: float
    step: int


@dataclass(slots=True)
class MLflowRunRecord:
    run_name: str
    tags: dict[str, str]
    params: dict[str, str]
    metrics: list[MetricPoint] = field(default_factory=list)
    artifacts: list[dict[str, str]] = field(default_factory=list)


def _flatten(prefix: str, value: Any, out: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            new_prefix = f"{prefix}.{key}" if prefix else str(key)
            _flatten(new_prefix, nested, out)
        return
    out[prefix] = str(value)


def build_mlflow_record(
    bundle: TrackingIdentityBundle,
    config: dict[str, Any],
    metrics: list[MetricPoint],
    git_commit: str,
    git_branch: str,
) -> MLflowRunRecord:
    params: dict[str, str] = {}
    _flatten("", config, params)
    tags = {
        "run_id": bundle.identity.run_id,
        "project_id": bundle.identity.project_id,
        "experiment_id": bundle.identity.experiment_id,
        "coordinator_run_id": bundle.identity.coordinator_run_id,
        "mlflow_run_id": bundle.identity.mlflow_run_id,
        "trace_id": bundle.identity.trace_id,
        "git_commit": git_commit,
        "git_branch": git_branch,
    }
    artifacts = [
        {
            "logical_name": artifact.logical_name,
            "uri": artifact.uri,
            "checksum": artifact.checksum,
            "media_type": artifact.media_type,
        }
        for artifact in bundle.artifacts
    ]
    return MLflowRunRecord(
        run_name=bundle.identity.run_id,
        tags=tags,
        params=params,
        metrics=metrics,
        artifacts=artifacts,
    )
