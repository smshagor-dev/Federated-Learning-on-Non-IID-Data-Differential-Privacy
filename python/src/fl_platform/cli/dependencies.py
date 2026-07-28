from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .configuration import LauncherPaths, ResolvedCompose


@dataclass(slots=True)
class CheckResult:
    ok: bool
    message: str


def check_python_version() -> CheckResult:
    if sys.version_info < (3, 11):
        return CheckResult(False, "Python 3.11 or newer is required.")
    return CheckResult(True, f"Python {sys.version.split()[0]}")


def resolve_npm_command() -> str:
    return "npm.cmd" if sys.platform == "win32" else "npm"


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def check_command(command: str, description: str) -> CheckResult:
    if not command_exists(command):
        return CheckResult(False, f"{description} is not installed or not on PATH.")
    return CheckResult(True, f"{description} detected.")


def run_version(command: list[str]) -> CheckResult:
    try:
        result = subprocess.run(command, capture_output=True, check=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        return CheckResult(False, f"{' '.join(command)} failed: {exc}")
    version = result.stdout.strip() or result.stderr.strip() or "ok"
    return CheckResult(True, version)


def check_docker_daemon() -> CheckResult:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        return CheckResult(False, f"Docker daemon unavailable: {exc}")
    return CheckResult(True, f"Docker daemon {result.stdout.strip()}")


def check_web_dependencies(paths: LauncherPaths) -> CheckResult:
    if (paths.web_dir / "node_modules").exists():
        return CheckResult(True, "web/node_modules present")
    return CheckResult(
        False, "web/node_modules missing. Run with --install-web or npm ci in web/."
    )


def check_required_repository_files(paths: LauncherPaths) -> CheckResult:
    required_paths = [
        paths.web_dir / "package.json",
        paths.compose_dir / "docker-compose.dev.yml",
        paths.python_src / "fl_platform" / "cli" / "application.py",
    ]
    missing = [
        str(path.relative_to(paths.repo_root))
        for path in required_paths
        if not path.exists()
    ]
    if missing:
        return CheckResult(
            False, f"required repository files missing: {', '.join(missing)}"
        )
    return CheckResult(True, "required repository files present")


def check_runtime_directories(paths: LauncherPaths) -> CheckResult:
    try:
        paths.state_dir.mkdir(parents=True, exist_ok=True)
        paths.log_dir.mkdir(parents=True, exist_ok=True)
        probe = paths.state_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult(False, f"runtime directories not writable: {exc}")
    return CheckResult(True, "runtime directories writable")


def check_secure_profile_pki(
    paths: LauncherPaths, compose: ResolvedCompose
) -> CheckResult:
    if compose.profile == "development":
        return CheckResult(True, "development profile does not require mounted dev PKI")

    required_paths = [
        paths.repo_root / "certs" / "dev" / "ca" / "ca.cert.pem",
        paths.repo_root / "certs" / "dev" / "services" / "coordinator" / "tls.cert.pem",
        paths.repo_root / "certs" / "dev" / "services" / "coordinator" / "tls.key.pem",
        paths.repo_root / "certs" / "dev" / "services" / "go-api" / "tls.cert.pem",
        paths.repo_root / "certs" / "dev" / "services" / "go-api" / "tls.key.pem",
        paths.repo_root / "certs" / "dev" / "workers" / "worker-1" / "tls.cert.pem",
        paths.repo_root / "certs" / "dev" / "workers" / "worker-1" / "tls.key.pem",
    ]

    if compose.profile in {
        "secure-cohort-handshake",
        "secure-user-level-dp",
        "secure-hybrid-dp",
        "secure-adaptive-clipping",
        "masked-update-runtime",
    }:
        required_paths.extend(
            [
                paths.repo_root
                / "certs"
                / "dev"
                / "workers"
                / "worker-2"
                / "tls.cert.pem",
                paths.repo_root
                / "certs"
                / "dev"
                / "workers"
                / "worker-2"
                / "tls.key.pem",
                paths.repo_root
                / "certs"
                / "dev"
                / "workers"
                / "worker-3"
                / "tls.cert.pem",
                paths.repo_root
                / "certs"
                / "dev"
                / "workers"
                / "worker-3"
                / "tls.key.pem",
            ]
        )

    missing = [
        str(path.relative_to(paths.repo_root))
        for path in required_paths
        if not path.exists()
    ]
    if missing:
        return CheckResult(
            False,
            "missing dev PKI material for profile "
            f"{compose.profile}: {', '.join(missing)}",
        )
    return CheckResult(
        True,
        f"dev PKI material present for profile {compose.profile}",
    )


def check_environment_configuration(
    paths: LauncherPaths, compose: ResolvedCompose
) -> CheckResult:
    missing_vars: list[str] = []
    if "api" in compose.services and not os.environ.get("FL_API_BASE_URL"):
        # This is informational only; the launcher can derive the API URL later.
        pass
    required_files = [
        paths.web_dir / "package-lock.json",
    ]
    missing_files = [
        str(path.relative_to(paths.repo_root))
        for path in required_files
        if not path.exists()
    ]
    if missing_vars or missing_files:
        details = missing_vars + missing_files
        return CheckResult(
            False, f"environment configuration incomplete: {', '.join(details)}"
        )
    return CheckResult(True, "environment configuration present")
