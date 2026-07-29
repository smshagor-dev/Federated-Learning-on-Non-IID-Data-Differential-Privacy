from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from .compose import (
    ComposeResolutionError,
    compose_base_command,
    inspect_service,
    resolve_compose,
)
from .configuration import LauncherPaths, ResolvedCompose, resolve_paths
from .dependencies import (
    check_command,
    check_docker_daemon,
    check_environment_configuration,
    check_python_version,
    check_required_repository_files,
    check_runtime_directories,
    check_secure_profile_pki,
    check_web_dependencies,
    resolve_npm_command,
    run_version,
)
from .environment import detect_web_runtime
from .health import http_ready, is_port_available, wait_for_backend, wait_for_web
from .output import Console
from .parser import build_parser
from .processes import start_web_process, stop_process
from .status import (
    delete_runtime_state,
    load_runtime_state,
    new_runtime_state,
    process_is_running,
    save_runtime_state,
)

AUTO_PORT_ENV_VARS: tuple[tuple[str, int, str], ...] = (
    ("FL_POSTGRES_HOST_PORT", 5432, "postgres"),
    ("FL_REDIS_HOST_PORT", 6379, "redis"),
    ("FL_MINIO_API_HOST_PORT", 9000, "minio"),
    ("FL_MINIO_CONSOLE_HOST_PORT", 9001, "minio-console"),
    ("FL_MLFLOW_HOST_PORT", 5000, "mlflow"),
    ("FL_COORDINATOR_HOST_PORT", 50051, "coordinator"),
    ("FL_API_HOST_PORT", 8080, "api"),
    ("FL_PROMETHEUS_HOST_PORT", 9090, "prometheus"),
    ("FL_GRAFANA_HOST_PORT", 3001, "grafana"),
    ("FL_OTEL_GRPC_HOST_PORT", 4317, "otel-grpc"),
    ("FL_OTEL_HTTP_HOST_PORT", 4318, "otel-http"),
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "start"
    paths = resolve_paths(Path(__file__).resolve().parents[4] / "main.py")
    console = Console(verbose=bool(getattr(args, "verbose", False)))

    try:
        if command == "start":
            return start_command(paths, console, args)
        if command == "restart":
            stop_command(paths, console, args, ignore_missing=True)
            return start_command(paths, console, args)
        if command == "stop":
            return stop_command(paths, console, args)
        if command == "status":
            return status_command(paths, console, args)
        if command == "health":
            return health_command(paths, console, args)
        if command == "doctor":
            return doctor_command(paths, console, args)
        if command == "logs":
            return logs_command(paths, console, args)
        if command == "build":
            return build_command(paths, console, args)
        if command == "clean":
            return clean_command(paths, console, args)
    except ComposeResolutionError as exc:
        console.error("compose", str(exc))
        return 2
    except KeyboardInterrupt:
        console.warning("launcher", "Interrupted")
        return 130

    parser.error(f"Unknown command: {command}")
    return 2


def start_command(paths: LauncherPaths, console: Console, args: Any) -> int:
    if args.no_cache:
        args.build = True
    duplicate = load_runtime_state(paths.state_file)
    if duplicate and (
        process_is_running(duplicate.launcher_pid)
        or process_is_running(duplicate.web_pid)
    ):
        console.warning(
            "launcher",
            "Platform appears to already be active. "
            "Use `python main.py status` or `python main.py stop`.",
        )
        return 1

    compose = resolve_runtime_compose(paths, console, args.profile)
    checks = run_startup_checks(paths, compose)
    for name, ok, message in checks:
        emit_check(console, name, ok, message)
    if not all(ok for _, ok, _ in checks):
        return 2

    port_check = validate_public_ports(compose, args.web_host, args.web_port)
    if port_check is not None:
        console.error("ports", port_check)
        return 2

    if compose is not None and args.build:
        console.info("compose", "Building backend images")
        build_args = compose_base_command(compose) + ["build"]
        if args.no_cache:
            build_args.append("--no-cache")
        build_args.extend(compose.backend_services)
        if run_checked(build_args, paths.repo_root, console, "compose") != 0:
            return 1

    if compose is not None:
        console.info("compose", "Starting backend containers")
        up_args = compose_base_command(compose) + ["up", "-d"]
        up_args.extend(compose.backend_services)
        if run_checked(up_args, paths.repo_root, console, "compose") != 0:
            return 1

        console.info("health", "Waiting for required backend services")
        backend_status = wait_for_backend(compose, paths, timeout_s=180)
        failed = [
            service
            for service in compose.required_services
            if not _service_ok(compose, service, backend_status)
        ]
        if failed:
            console.error(
                "health",
                f"Required services did not become ready: {', '.join(failed)}",
            )
            if not args.keep_backend:
                _compose_down(compose, paths, console, with_volumes=False)
            return 1
    else:
        console.warning(
            "launcher",
            "Docker backend unavailable. Starting dashboard in web-only mode.",
        )

    if args.install_web:
        console.info("web", "Installing locked web dependencies")
        if (
            run_checked([resolve_npm_command(), "ci"], paths.web_dir, console, "web")
            != 0
        ):
            if compose is not None and not args.keep_backend:
                _compose_down(compose, paths, console, with_volumes=False)
            return 1

    api_url = (
        compose.services["api"].published_url
        if compose is not None and "api" in compose.services
        else (
            os.environ.get("FL_API_BASE_URL")
            or os.environ.get("NEXT_PUBLIC_FL_API_BASE_URL")
            or "http://127.0.0.1:8080"
        )
    )
    web_runtime = detect_web_runtime(paths, args.web_host, args.web_port, api_url)
    console.info(
        "web", f"Starting local Next.js dev server with {web_runtime.package_manager}"
    )
    managed_web = start_web_process(
        web_runtime.command,
        paths.web_dir,
        web_runtime.env,
        paths.web_log_file,
        console,
    )

    if not wait_for_web(args.web_host, args.web_port, timeout_s=120):
        console.error("web", "Web server did not become ready in time.")
        stop_process(managed_web.process, console, "web")
        if compose is not None and not args.keep_backend:
            _compose_down(compose, paths, console, with_volumes=False)
        return 1

    state = new_runtime_state(
        compose_project_name=compose.project_name if compose is not None else "",
        profile=compose.profile if compose is not None else "web-only",
        compose_files=[str(item) for item in compose.compose_files]
        if compose is not None
        else [],
        backend_services=compose.backend_services if compose is not None else [],
        web_pid=managed_web.process.pid,
        web_port=args.web_port,
        keep_backend=args.keep_backend if compose is not None else False,
    )
    save_runtime_state(paths.state_file, state)
    print_platform_summary(compose, api_url, args.web_host, args.web_port, console)

    exit_code = 0
    try:
        while True:
            if managed_web.process.poll() is not None:
                exit_code = managed_web.process.returncode or 1
                console.error(
                    "web", f"Web process exited unexpectedly with code {exit_code}."
                )
                break
            time.sleep(2)
    except KeyboardInterrupt:
        console.info("launcher", "Ctrl+C received. Shutting down.")
    finally:
        stop_process(managed_web.process, console, "web")
        if compose is None:
            delete_runtime_state(paths.state_file)
        elif not args.keep_backend:
            _compose_down(compose, paths, console, with_volumes=False)
            delete_runtime_state(paths.state_file)
        else:
            save_runtime_state(
                paths.state_file,
                new_runtime_state(
                    compose_project_name=compose.project_name,
                    profile=compose.profile,
                    compose_files=[str(item) for item in compose.compose_files],
                    backend_services=compose.backend_services,
                    web_pid=None,
                    web_port=args.web_port,
                    keep_backend=True,
                ),
            )
            console.success("compose", "Backend kept running by request.")
    return exit_code


def stop_command(
    paths: LauncherPaths,
    console: Console,
    args: Any,
    ignore_missing: bool = False,
) -> int:
    state = load_runtime_state(paths.state_file)
    if state and not state.backend_services:
        if state.web_pid and process_is_running(state.web_pid):
            console.info("web", "Stopping managed web process from runtime state")
            with suppress(OSError):
                subprocess.run(
                    ["taskkill", "/PID", str(state.web_pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                )
        delete_runtime_state(paths.state_file)
        console.success("launcher", "Web-only platform stopped.")
        return 0
    profile = (
        args.profile
        if getattr(args, "profile", None)
        else (state.profile if state else None)
    )
    compose = resolve_compose(paths, profile)
    if state and process_is_running(state.web_pid):
        console.info("web", "Stopping managed web process from runtime state")
        with suppress(OSError):
            subprocess.run(
                ["taskkill", "/PID", str(state.web_pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )
    backend_present = any(
        inspect_service(compose, paths.repo_root, service).container_id
        for service in compose.backend_services
    )
    if not backend_present and not state:
        if ignore_missing:
            return 0
        console.info(
            "launcher", "No managed runtime state or backend containers found."
        )
        return 0
    _compose_down(compose, paths, console, with_volumes=False)
    delete_runtime_state(paths.state_file)
    console.success("launcher", "Platform stopped. Named volumes were preserved.")
    return 0


def status_command(paths: LauncherPaths, console: Console, args: Any) -> int:
    state = load_runtime_state(paths.state_file)
    if state and not state.backend_services:
        payload: dict[str, Any] = {
            "profile": state.profile,
            "project": state.compose_project_name,
            "services": [],
            "web_pid": state.web_pid,
            "web_process_running": process_is_running(state.web_pid),
        }
        if args.json_output:
            console.plain(json.dumps(payload, indent=2))
        else:
            console.plain("Federated Learning Research Platform status")
            web_state = "running" if payload["web_process_running"] else "stopped"
            console.plain(
                f"{'web-process':<18} {web_state:<12} {'local':<10} "
                f"pid={payload['web_pid']}"
            )
            console.plain("backend             skipped      web-only   -")
        return 0
    profile = (
        args.profile
        if getattr(args, "profile", None)
        else (state.profile if state else None)
    )
    compose = resolve_compose(paths, profile)
    payload: dict[str, Any] = {
        "profile": compose.profile,
        "project": compose.project_name,
        "services": [],
    }
    for service in compose.services:
        status = inspect_service(compose, paths.repo_root, service)
        item = {
            "service": service,
            "state": status.state,
            "health": status.health,
            "container_id": status.container_id,
            "url": compose.services[service].published_url,
        }
        payload["services"].append(item)
    payload["web_pid"] = state.web_pid if state else None
    payload["web_process_running"] = process_is_running(
        state.web_pid if state else None
    )
    if args.json_output:
        console.plain(json.dumps(payload, indent=2))
    else:
        console.plain("Federated Learning Research Platform status")
        for item in payload["services"]:
            console.plain(
                f"{item['service']:<18} "
                f"{item['state']:<12} "
                f"{item['health']:<10} "
                f"{item['url'] or '-'}"
            )
        web_state = "running" if payload["web_process_running"] else "stopped"
        console.plain(
            f"{'web-process':<18} {web_state:<12} {'local':<10} "
            f"pid={payload['web_pid']}"
        )
    return 0


def health_command(paths: LauncherPaths, console: Console, args: Any) -> int:
    state = load_runtime_state(paths.state_file)
    if state and not state.backend_services:
        web_port = state.web_port
        web_ok = http_ready("127.0.0.1", web_port, "/")
        payload = {
            "ok": web_ok,
            "checks": [
                {
                    "service": "web",
                    "state": "running" if web_ok else "unavailable",
                    "health": "ready" if web_ok else "down",
                    "ok": str(web_ok).lower(),
                },
                {
                    "service": "backend",
                    "state": "skipped",
                    "health": "web-only",
                    "ok": "true",
                },
            ],
        }
        if args.json_output:
            console.plain(json.dumps(payload, indent=2))
        else:
            for item in payload["checks"]:
                console.plain(
                    f"{item['service']:<18} ok={item['ok']:<5} "
                    f"state={item['state']:<12} health={item['health']}"
                )
        return 0 if web_ok else 1
    profile = (
        args.profile
        if getattr(args, "profile", None)
        else (state.profile if state else None)
    )
    compose = resolve_compose(paths, profile)
    results: list[dict[str, str]] = []
    overall_ok = True
    for service in compose.required_services:
        status = inspect_service(compose, paths.repo_root, service)
        ok = _service_ok(compose, service, {service: status})
        overall_ok = overall_ok and ok
        results.append(
            {
                "service": service,
                "state": status.state,
                "health": status.health,
                "ok": str(ok).lower(),
            }
        )
    web_port = state.web_port if state else 3000
    web_ok = http_ready("127.0.0.1", web_port, "/")
    results.append(
        {
            "service": "web",
            "state": "running" if web_ok else "unavailable",
            "health": "ready" if web_ok else "down",
            "ok": str(web_ok).lower(),
        }
    )
    overall_ok = overall_ok and web_ok
    if args.json_output:
        console.plain(json.dumps({"ok": overall_ok, "checks": results}, indent=2))
    else:
        for item in results:
            console.plain(
                f"{item['service']:<18} ok={item['ok']:<5} "
                f"state={item['state']:<12} health={item['health']}"
            )
    return 0 if overall_ok else 1


def doctor_command(paths: LauncherPaths, console: Console, args: Any) -> int:
    compose = resolve_compose(paths, args.profile)
    checks = run_startup_checks(paths, compose)
    if args.json_output:
        console.plain(
            json.dumps(
                [
                    {"name": name, "ok": ok, "message": message}
                    for name, ok, message in checks
                ],
                indent=2,
            )
        )
    else:
        for name, ok, message in checks:
            emit_check(console, name, ok, message)
    return 0 if all(ok for _, ok, _ in checks) else 1


def logs_command(paths: LauncherPaths, console: Console, args: Any) -> int:
    state = load_runtime_state(paths.state_file)
    profile = (
        args.profile
        if getattr(args, "profile", None)
        else (state.profile if state else None)
    )
    compose = resolve_compose(paths, profile)
    if args.service == "web":
        if not paths.web_log_file.exists():
            console.error("logs", "No managed web log file exists yet.")
            return 1
        console.plain(paths.web_log_file.read_text(encoding="utf-8"))
        return 0
    if args.service and args.service not in compose.services:
        console.error("logs", f"Unknown service '{args.service}'.")
        return 1
    command = compose_base_command(compose) + ["logs"]
    if args.follow:
        command.append("--follow")
    if args.service:
        command.append(args.service)
    return run_checked(command, paths.repo_root, console, "logs")


def build_command(paths: LauncherPaths, console: Console, args: Any) -> int:
    compose = resolve_compose(paths, args.profile)
    services = args.services or compose.backend_services
    unknown = [
        service_name
        for service_name in services
        if service_name not in compose.services or compose.services[service_name].web
    ]
    if unknown:
        console.error(
            "build",
            f"Unknown or unsupported backend build target(s): {', '.join(unknown)}",
        )
        return 1
    command = compose_base_command(compose) + ["build"]
    if args.no_cache:
        command.append("--no-cache")
    command.extend(services)
    return run_checked(command, paths.repo_root, console, "build")


def clean_command(paths: LauncherPaths, console: Console, args: Any) -> int:
    compose = resolve_compose(paths, args.profile)
    if args.volumes and not args.yes:
        console.error("clean", "Refusing destructive volume cleanup without --yes.")
        return 1
    _compose_down(compose, paths, console, with_volumes=bool(args.volumes))
    delete_runtime_state(paths.state_file)
    if paths.web_log_file.exists():
        paths.web_log_file.unlink()
    console.success("clean", "Cleanup completed.")
    return 0


def run_dependency_checks(
    paths: LauncherPaths, compose: ResolvedCompose
) -> list[tuple[str, bool, str]]:
    python_check = check_python_version()
    docker_check = check_command("docker", "Docker CLI")
    docker_daemon_check = check_docker_daemon()
    compose_version_check = run_version(["docker", "compose", "version"])
    node_check = check_command("node", "Node.js")
    npm_check = check_command(resolve_npm_command(), "npm")
    web_dependency_check = check_web_dependencies(paths)
    repository_files_check = check_required_repository_files(paths)
    runtime_dir_check = check_runtime_directories(paths)
    environment_check = check_environment_configuration(paths, compose)
    pki_check = check_secure_profile_pki(paths, compose)
    legacy_exists = paths.legacy_main.exists()
    checks = [
        ("python", python_check.ok, python_check.message),
        ("docker", docker_check.ok, docker_check.message),
        ("docker-daemon", docker_daemon_check.ok, docker_daemon_check.message),
        ("docker-compose", compose_version_check.ok, compose_version_check.message),
        ("node", node_check.ok, node_check.message),
        ("npm", npm_check.ok, npm_check.message),
        ("web-deps", web_dependency_check.ok, web_dependency_check.message),
        (
            "repo-files",
            repository_files_check.ok,
            repository_files_check.message,
        ),
        ("runtime-dirs", runtime_dir_check.ok, runtime_dir_check.message),
        ("env-config", environment_check.ok, environment_check.message),
        ("pki", pki_check.ok, pki_check.message),
        (
            "legacy-main",
            legacy_exists,
            "legacy prototype located" if legacy_exists else "legacy prototype missing",
        ),
        (
            "compose-config",
            True,
            f"resolved {len(compose.services)} services from profile {compose.profile}",
        ),
    ]
    return [(name, bool(ok), str(message)) for name, ok, message in checks]


def run_startup_checks(
    paths: LauncherPaths, compose: ResolvedCompose | None
) -> list[tuple[str, bool, str]]:
    if compose is None:
        python_check = check_python_version()
        node_check = check_command("node", "Node.js")
        npm_check = check_command(resolve_npm_command(), "npm")
        web_dependency_check = check_web_dependencies(paths)
        repository_files_check = check_required_repository_files(paths)
        runtime_dir_check = check_runtime_directories(paths)
        legacy_exists = paths.legacy_main.exists()
        checks = [
            ("python", python_check.ok, python_check.message),
            ("node", node_check.ok, node_check.message),
            ("npm", npm_check.ok, npm_check.message),
            ("web-deps", web_dependency_check.ok, web_dependency_check.message),
            (
                "repo-files",
                repository_files_check.ok,
                repository_files_check.message,
            ),
            ("runtime-dirs", runtime_dir_check.ok, runtime_dir_check.message),
            (
                "legacy-main",
                legacy_exists,
                "legacy prototype located"
                if legacy_exists
                else "legacy prototype missing",
            ),
            (
                "backend-mode",
                True,
                "Docker unavailable; launcher will continue in web-only mode.",
            ),
        ]
        return [(name, bool(ok), str(message)) for name, ok, message in checks]
    return run_dependency_checks(paths, compose)


def emit_check(console: Console, name: str, ok: bool, message: str) -> None:
    if ok:
        console.success(name, message)
    else:
        console.error(name, message)


def validate_public_ports(
    compose: ResolvedCompose | None, web_host: str, web_port: int
) -> str | None:
    if compose is not None:
        for service_name, definition in compose.services.items():
            if definition.web:
                continue
            if definition.published_port is None:
                continue
            if not is_port_available("127.0.0.1", definition.published_port):
                return (
                    f"Port {definition.published_port} for {service_name} "
                    "is already in use."
                )
    if not is_port_available(web_host, web_port):
        return f"Web port {web_port} is already in use."
    return None


def print_platform_summary(
    compose: ResolvedCompose | None,
    api_url: str,
    web_host: str,
    web_port: int,
    console: Console,
) -> None:
    console.plain("")
    console.plain("Federated Learning Research Platform")
    console.plain("")
    console.plain("Backend")
    console.plain("--------------------------------------------------")
    if compose is None:
        console.plain("Backend skipped      WEB-ONLY")
    else:
        for service in compose.backend_services:
            definition = compose.services[service]
            status = inspect_service(
                compose, compose.compose_files[0].parent.parent.parent, service
            )
            health = status.health if status.health != "none" else status.state
            console.plain(f"{definition.display_name:<18} {health.upper()}")
    console.plain("")
    console.plain("URLs")
    console.plain("--------------------------------------------------")
    console.plain(f"Web Dashboard      http://{web_host}:{web_port}")
    if compose is None:
        console.plain(f"API Base URL        {api_url} (not managed by launcher)")
    else:
        for service_definition in compose.services.values():
            if service_definition.web or not service_definition.published_url:
                continue
            console.plain(
                f"{service_definition.display_name:<18} {service_definition.published_url}"
            )
    console.plain("")
    if compose is None:
        console.plain("Press Ctrl+C to stop the local dashboard.")
    else:
        console.plain("Press Ctrl+C to stop the complete platform.")


def run_checked(command: list[str], cwd: Path, console: Console, component: str) -> int:
    console.debug(component, "Running: " + " ".join(command))
    result = subprocess.run(command, cwd=cwd, shell=False)
    if result.returncode != 0:
        console.error(component, f"Command failed with exit code {result.returncode}")
    return result.returncode


def _compose_down(
    compose: ResolvedCompose,
    paths: LauncherPaths,
    console: Console,
    with_volumes: bool,
) -> None:
    command = compose_base_command(compose) + ["down", "--remove-orphans"]
    if with_volumes:
        command.append("--volumes")
    run_checked(command, paths.repo_root, console, "compose")


def _service_ok(
    compose: ResolvedCompose, service: str, statuses: dict[str, Any]
) -> bool:
    status = statuses[service]
    if status.state != "running":
        return False
    if status.health not in {"healthy", "none"}:
        return False
    definition = compose.services[service]
    if definition.category.name == "API" and definition.published_port is not None:
        return http_ready("127.0.0.1", definition.published_port, "/healthz")
    return True


def resolve_runtime_compose(
    paths: LauncherPaths,
    console: Console,
    profile: str | None,
) -> ResolvedCompose | None:
    docker_check = check_command("docker", "Docker CLI")
    if not docker_check.ok:
        console.warning(
            "docker",
            "Docker CLI not available. Backend startup will be skipped.",
        )
        return None

    docker_daemon_check = check_docker_daemon()
    if docker_daemon_check.ok:
        apply_automatic_port_overrides(console)
        return resolve_compose(paths, profile)

    console.warning("docker-daemon", docker_daemon_check.message)
    if _try_start_docker_desktop(console):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            ready_check = check_docker_daemon()
            if ready_check.ok:
                console.success("docker-daemon", ready_check.message)
                apply_automatic_port_overrides(console)
                return resolve_compose(paths, profile)
            time.sleep(3)
        console.warning(
            "docker-daemon",
            "Docker Desktop was launched, but the daemon did not become ready in time.",
        )
    return None


def _try_start_docker_desktop(console: Console) -> bool:
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "Docker" / "Docker" / "Docker Desktop.exe",
        Path(os.environ.get("LocalAppData", "")) / "Programs" / "Docker" / "Docker" / "Docker Desktop.exe",
    ]
    for candidate in candidates:
        if not str(candidate):
            continue
        if not candidate.exists():
            continue
        try:
            console.info("docker-daemon", f"Launching Docker Desktop from {candidate}")
            subprocess.Popen(
                [str(candidate)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
            return True
        except OSError:
            continue
    console.warning(
        "docker-daemon",
        "Docker CLI is installed, but Docker Desktop could not be auto-started.",
    )
    return False


def apply_automatic_port_overrides(console: Console) -> None:
    for env_var, default_port, service_name in AUTO_PORT_ENV_VARS:
        configured_port = os.environ.get(env_var)
        if configured_port:
            continue
        if is_port_available("127.0.0.1", default_port):
            continue
        replacement = _find_available_port(default_port)
        if replacement is None:
            continue
        os.environ[env_var] = str(replacement)
        console.warning(
            "ports",
            f"Port {default_port} for {service_name} is busy; using {replacement} via {env_var}.",
        )


def _find_available_port(preferred_port: int, attempts: int = 50) -> int | None:
    for candidate in range(preferred_port + 1, preferred_port + attempts + 1):
        if is_port_available("127.0.0.1", candidate):
            return candidate
    fallback = preferred_port + 10_000
    for candidate in range(fallback, fallback + attempts):
        if is_port_available("127.0.0.1", candidate):
            return candidate
    return None
