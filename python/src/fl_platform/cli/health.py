from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from http.client import HTTPConnection

from .compose import ContainerStatus, inspect_service
from .configuration import LauncherPaths, ResolvedCompose, ServiceCategory


@dataclass(slots=True)
class HealthCheckResult:
    ok: bool
    message: str


def is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


def wait_for_backend(
    compose: ResolvedCompose, paths: LauncherPaths, timeout_s: int = 180
) -> dict[str, ContainerStatus]:
    deadline = time.monotonic() + timeout_s
    latest: dict[str, ContainerStatus] = {}
    while time.monotonic() < deadline:
        latest = {
            service: inspect_service(compose, paths.repo_root, service)
            for service in compose.required_services
        }
        if all(
            is_container_ready(compose, latest[service])
            for service in compose.required_services
        ):
            return latest
        time.sleep(2)
    return latest


def is_container_ready(compose: ResolvedCompose, status: ContainerStatus) -> bool:
    if status.state != "running":
        return False
    definition = compose.services[status.service]
    if status.health not in {"none", "healthy"}:
        return False
    if (
        definition.category is ServiceCategory.API
        and definition.published_port is not None
    ):
        return http_ready("127.0.0.1", definition.published_port, "/healthz")
    if (
        definition.category is ServiceCategory.OBSERVABILITY
        and status.service == "prometheus"
        and definition.published_port is not None
    ):
        return http_ready("127.0.0.1", definition.published_port, "/-/ready")
    if (
        definition.category is ServiceCategory.OBSERVABILITY
        and status.service == "grafana"
        and definition.published_port is not None
    ):
        return http_ready("127.0.0.1", definition.published_port, "/api/health")
    return True


def wait_for_web(host: str, port: int, timeout_s: int = 120) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if http_ready(host, port, "/"):
            return True
        time.sleep(1)
    return False


def http_ready(host: str, port: int, path: str) -> bool:
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", path)
        response = conn.getresponse()
        response.read()
        conn.close()
        return response.status < 500
    except OSError:
        return False
