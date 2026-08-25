#!/usr/bin/env python3
"""Validate deterministic v3 Kubernetes release-contract requirements."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
K8S = ROOT / "infra" / "kubernetes"


class InfrastructureValidationError(RuntimeError):
    pass


def _read(name: str) -> str:
    path = K8S / name
    if not path.is_file():
        raise InfrastructureValidationError(f"missing Kubernetes manifest: {name}")
    return path.read_text(encoding="utf-8")


def _require(text: str, needle: str, *, context: str) -> None:
    if needle not in text:
        raise InfrastructureValidationError(f"{context}: missing {needle!r}")


def _require_count(text: str, needle: str, count: int, *, context: str) -> None:
    actual = text.count(needle)
    if actual < count:
        raise InfrastructureValidationError(
            f"{context}: expected at least {count} occurrences of {needle!r}, "
            f"found {actual}"
        )


def validate() -> tuple[str, ...]:
    api = _read("api-deployment.yaml")
    worker = _read("python-worker-deployment.yaml")
    stateful = _read("stateful-services.yaml")
    resilience = _read("v3-resilience.yaml")
    controls = _read("v3-production-controls.yaml")

    _require(api, "replicas: 2", context="api")
    _require(api, "maxUnavailable: 0", context="api")
    _require(api, "topologySpreadConstraints:", context="api")
    _require(api, "readinessProbe:", context="api")
    _require(api, "livenessProbe:", context="api")
    _require(api, "resources:", context="api")

    _require(worker, "replicas: 2", context="python-worker")
    _require(worker, "topologySpreadConstraints:", context="python-worker")
    _require(worker, "resources:", context="python-worker")

    _require(stateful, "secretKeyRef:", context="postgres")
    _require(stateful, "name: fl-platform-postgres", context="postgres")
    _require_count(stateful, "volumeClaimTemplates:", 2, context="stateful services")
    _require_count(stateful, "readinessProbe:", 2, context="stateful services")
    _require_count(stateful, "livenessProbe:", 2, context="stateful services")
    _require(stateful, "--appendonly", context="redis")
    _require(stateful, '"yes"', context="redis")

    inline_password = re.search(
        r"name:\s*POSTGRES_PASSWORD\s*\n\s*value:\s*[^\n]+",
        stateful,
    )
    if inline_password is not None:
        raise InfrastructureValidationError(
            "postgres password must come from a Secret, not inline YAML"
        )

    _require_count(resilience, "kind: PodDisruptionBudget", 2, context="resilience")
    _require(resilience, "app: api", context="api PDB")
    _require(resilience, "app: python-worker", context="worker PDB")

    _require(controls, "kind: ResourceQuota", context="production controls")
    _require(controls, "kind: LimitRange", context="production controls")
    _require_count(
        controls,
        "kind: HorizontalPodAutoscaler",
        2,
        context="production controls",
    )
    _require(controls, "minReplicas: 2", context="production controls")

    warnings: list[str] = []
    for name, text in (
        ("api-deployment.yaml", api),
        ("python-worker-deployment.yaml", worker),
    ):
        if re.search(r"image:\s*[^\s]+:latest\b", text):
            warnings.append(
                f"{name}: image tag is not release-pinned; supply-chain gate remains open"
            )
    return tuple(warnings)


def main() -> int:
    warnings = validate()
    print("v3 infrastructure contract: PASS")
    for warning in warnings:
        print(f"WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
