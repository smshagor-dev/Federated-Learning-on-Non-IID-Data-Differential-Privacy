from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from .configuration import (
    DEFAULT_PROFILE,
    PROFILE_TO_FILES,
    LauncherPaths,
    ResolvedCompose,
    ServiceCategory,
    ServiceDefinition,
)


class ComposeResolutionError(RuntimeError):
    pass


def resolve_compose(paths: LauncherPaths, profile: str | None) -> ResolvedCompose:
    selected_profile = profile or DEFAULT_PROFILE
    if selected_profile not in PROFILE_TO_FILES:
        supported = ", ".join(sorted(PROFILE_TO_FILES))
        raise ComposeResolutionError(
            f"Unsupported profile '{selected_profile}'. Supported profiles: {supported}"
        )
    compose_files = [
        paths.compose_dir / name for name in PROFILE_TO_FILES[selected_profile]
    ]
    for compose_file in compose_files:
        if not compose_file.exists():
            raise ComposeResolutionError(f"Compose file missing: {compose_file}")
    project_name = "federated_dp_research"
    payload = read_compose_config(paths.repo_root, compose_files, project_name)
    services = build_service_inventory(payload)
    backend_services = [
        name for name, item in services.items() if item.backend and not item.web
    ]
    required_services = [
        name
        for name, item in services.items()
        if item.required and item.backend and not item.web
    ]
    optional_services = [
        name
        for name, item in services.items()
        if not item.required and item.backend and not item.web
    ]
    named_volumes = sorted((payload.get("volumes") or {}).keys())
    return ResolvedCompose(
        profile=selected_profile,
        compose_files=compose_files,
        project_name=project_name,
        services=services,
        backend_services=backend_services,
        required_services=required_services,
        optional_services=optional_services,
        named_volumes=named_volumes,
    )


def compose_base_command(compose: ResolvedCompose) -> list[str]:
    command = ["docker", "compose", "-p", compose.project_name]
    for compose_file in compose.compose_files:
        command.extend(["-f", str(compose_file)])
    return command


def read_compose_config(
    repo_root: Path, compose_files: list[Path], project_name: str
) -> dict[str, Any]:
    command = ["docker", "compose", "-p", project_name]
    for compose_file in compose_files:
        command.extend(["-f", str(compose_file)])
    command.append("config")
    result = subprocess.run(command, cwd=repo_root, capture_output=True, text=True)
    if result.returncode != 0:
        raise ComposeResolutionError(
            result.stderr.strip() or "docker compose config failed"
        )
    loaded = yaml.safe_load(result.stdout)
    if not isinstance(loaded, dict):
        raise ComposeResolutionError("docker compose config did not return a mapping")
    return loaded


def build_service_inventory(payload: dict[str, Any]) -> dict[str, ServiceDefinition]:
    services = payload.get("services") or {}
    inventory: dict[str, ServiceDefinition] = {}
    for name, raw_service in services.items():
        ports = raw_service.get("ports") or []
        published_port = parse_published_port(ports)
        published_url = default_url_for_service(name, published_port)
        category = categorize_service(name)
        web = category is ServiceCategory.WEB
        required = name in {
            "postgres",
            "redis",
            "coordinator",
            "api",
            "research-writer",
            "python-worker",
        } or name.startswith("worker-")
        observability = name in {"prometheus", "grafana", "otel-collector"}
        infrastructure = name in {"postgres", "redis", "minio", "mlflow"}
        inventory[name] = ServiceDefinition(
            compose_service=name,
            display_name=name.replace("-", " ").title(),
            category=category,
            required=required,
            backend=not web,
            observability=observability,
            infrastructure=infrastructure,
            web=web,
            published_port=published_port,
            published_url=published_url,
        )
    return inventory


def parse_published_port(ports: list[Any]) -> int | None:
    for port in ports:
        if isinstance(port, dict):
            published = port.get("published")
            if published is not None:
                return int(str(published))
        elif isinstance(port, str):
            published = str(port).split(":")[0]
            if published:
                return int(published)
    return None


def default_url_for_service(name: str, published_port: int | None) -> str | None:
    if published_port is None:
        return None
    if name in {"api", "research-writer"}:
        return f"http://127.0.0.1:{published_port}"
    if name == "grafana":
        return f"http://127.0.0.1:{published_port}"
    if name == "prometheus":
        return f"http://127.0.0.1:{published_port}"
    if name == "web":
        return f"http://127.0.0.1:{published_port}"
    if name == "minio":
        return f"http://127.0.0.1:{published_port}"
    if name == "mlflow":
        return f"http://127.0.0.1:{published_port}"
    return None


def categorize_service(name: str) -> ServiceCategory:
    if name == "postgres":
        return ServiceCategory.DATABASE
    if name == "redis":
        return ServiceCategory.CACHE
    if name == "coordinator":
        return ServiceCategory.COORDINATOR
    if name == "api":
        return ServiceCategory.API
    if name == "research-writer":
        return ServiceCategory.RESEARCH_WRITER
    if name == "web":
        return ServiceCategory.WEB
    if name == "python-worker" or name.startswith("worker-"):
        return ServiceCategory.WORKER
    if name in {"prometheus", "grafana", "otel-collector"}:
        return ServiceCategory.OBSERVABILITY
    if name in {"minio", "mlflow"}:
        return ServiceCategory.STORAGE
    return ServiceCategory.OTHER


@dataclass(slots=True)
class ContainerStatus:
    service: str
    container_id: str | None
    state: str
    health: str
    started_at: str | None


def inspect_service(
    compose: ResolvedCompose, repo_root: Path, service: str
) -> ContainerStatus:
    base = compose_base_command(compose)
    ps = subprocess.run(
        base + ["ps", "-q", service], cwd=repo_root, capture_output=True, text=True
    )
    container_id = ps.stdout.strip() or None
    if not container_id:
        return ContainerStatus(
            service=service,
            container_id=None,
            state="not_created",
            health="unknown",
            started_at=None,
        )
    try:
        inspect = subprocess.run(
            ["docker", "inspect", container_id],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return ContainerStatus(
            service=service,
            container_id=container_id,
            state="not_created",
            health="unknown",
            started_at=None,
        )
    payload = json.loads(inspect.stdout)[0]
    state = payload["State"]["Status"]
    health = payload["State"].get("Health", {}).get("Status", "none")
    started_at = payload["State"].get("StartedAt")
    return ContainerStatus(
        service=service,
        container_id=container_id,
        state=state,
        health=health,
        started_at=started_at,
    )
