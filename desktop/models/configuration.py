from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AppPaths:
    project_root: str
    main_script: str
    config_path: str
    results_dir: str
    database_path: str
    preferences_path: str
