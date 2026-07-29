from __future__ import annotations

import os
from copy import deepcopy

import yaml


class ConfigurationService:
    def __init__(self, config_path: str, project_root: str) -> None:
        self.config_path = config_path
        self.project_root = project_root

    def load(self, path: str | None = None) -> dict:
        target = path or self.config_path
        with open(target, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    def build_runtime_config(self, base_config: dict, updates: dict) -> dict:
        config = deepcopy(base_config)
        for section, values in updates.items():
            config[section].update(values)
        return config

    def write_runtime_config(self, config: dict, results_dir: str) -> str:
        os.makedirs(results_dir, exist_ok=True)
        target = os.path.join(results_dir, "_desktop_runtime_config.yaml")
        with open(target, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False)
        return target
