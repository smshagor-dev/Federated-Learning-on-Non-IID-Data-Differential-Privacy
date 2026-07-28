from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ServiceCategory(str, Enum):
    DATABASE = "DATABASE"
    CACHE = "CACHE"
    COORDINATOR = "COORDINATOR"
    API = "API"
    WORKER = "WORKER"
    RESEARCH_WRITER = "RESEARCH_WRITER"
    OBSERVABILITY = "OBSERVABILITY"
    STORAGE = "STORAGE"
    WEB = "WEB"
    OTHER = "OTHER"


@dataclass(slots=True)
class LauncherPaths:
    repo_root: Path
    python_src: Path
    web_dir: Path
    compose_dir: Path
    state_dir: Path
    log_dir: Path
    state_file: Path
    web_log_file: Path
    legacy_main: Path


@dataclass(slots=True)
class ServiceDefinition:
    compose_service: str
    display_name: str
    category: ServiceCategory
    required: bool
    backend: bool
    observability: bool
    infrastructure: bool
    web: bool
    published_port: int | None
    published_url: str | None


@dataclass(slots=True)
class ResolvedCompose:
    profile: str
    compose_files: list[Path]
    project_name: str
    services: dict[str, ServiceDefinition]
    backend_services: list[str]
    required_services: list[str]
    optional_services: list[str]
    named_volumes: list[str]


DEFAULT_PROFILE = "development"

PROFILE_TO_FILES: dict[str, tuple[str, ...]] = {
    "development": ("docker-compose.dev.yml",),
    "security": ("docker-compose.dev.yml", "docker-compose.security.yml"),
    "secure-cohort-handshake": (
        "docker-compose.dev.yml",
        "docker-compose.security.yml",
        "docker-compose.secure-cohort-handshake.yml",
    ),
    "secure-user-level-dp": (
        "docker-compose.dev.yml",
        "docker-compose.security.yml",
        "docker-compose.secure-cohort-handshake.yml",
        "docker-compose.secure-user-level-dp.yml",
    ),
    "secure-hybrid-dp": (
        "docker-compose.dev.yml",
        "docker-compose.security.yml",
        "docker-compose.secure-cohort-handshake.yml",
        "docker-compose.secure-hybrid-dp.yml",
    ),
    "secure-adaptive-clipping": (
        "docker-compose.dev.yml",
        "docker-compose.security.yml",
        "docker-compose.secure-cohort-handshake.yml",
        "docker-compose.secure-adaptive-clipping.yml",
    ),
    "masked-update-runtime": (
        "docker-compose.dev.yml",
        "docker-compose.security.yml",
        "docker-compose.secure-cohort-handshake.yml",
        "docker-compose.masked-update-runtime.yml",
    ),
}


def resolve_paths(entrypoint: Path) -> LauncherPaths:
    repo_root = entrypoint.resolve().parent
    state_dir = repo_root / ".tmp"
    log_dir = state_dir / "logs"
    return LauncherPaths(
        repo_root=repo_root,
        python_src=repo_root / "python" / "src",
        web_dir=repo_root / "web",
        compose_dir=repo_root / "infra" / "compose",
        state_dir=state_dir,
        log_dir=log_dir,
        state_file=state_dir / "platform-runtime.json",
        web_log_file=log_dir / "web.log",
        legacy_main=repo_root / "legacy" / "python-research-studio" / "main.py",
    )
