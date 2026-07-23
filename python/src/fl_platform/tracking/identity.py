from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RunIdentity:
    run_id: str
    project_id: str
    experiment_id: str
    coordinator_run_id: str
    mlflow_run_id: str
    trace_id: str


@dataclass(slots=True)
class ArtifactRef:
    logical_name: str
    uri: str
    checksum: str = ""
    media_type: str = "application/octet-stream"


@dataclass(slots=True)
class TrackingIdentityBundle:
    identity: RunIdentity
    artifacts: list[ArtifactRef] = field(default_factory=list)

    def add_artifact(self, artifact: ArtifactRef) -> None:
        self.artifacts.append(artifact)

    def artifact_map(self) -> dict[str, ArtifactRef]:
        return {artifact.logical_name: artifact for artifact in self.artifacts}
