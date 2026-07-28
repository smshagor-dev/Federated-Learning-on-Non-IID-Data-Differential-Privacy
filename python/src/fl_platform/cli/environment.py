from __future__ import annotations

import os
from dataclasses import dataclass

from .configuration import LauncherPaths
from .dependencies import resolve_npm_command


@dataclass(slots=True)
class WebRuntime:
    package_manager: str
    command: list[str]
    env: dict[str, str]


def detect_web_runtime(
    paths: LauncherPaths, host: str, port: int, api_url: str
) -> WebRuntime:
    package_manager = resolve_npm_command()
    env = dict(os.environ)
    env["HOST"] = host
    env["PORT"] = str(port)
    env["FL_API_BASE_URL"] = api_url
    env["NEXT_PUBLIC_FL_API_BASE_URL"] = api_url
    return WebRuntime(
        package_manager=package_manager,
        command=[
            package_manager,
            "run",
            "dev",
            "--",
            "--hostname",
            host,
            "--port",
            str(port),
        ],
        env=env,
    )
