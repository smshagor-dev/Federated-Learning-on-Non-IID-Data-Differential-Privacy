from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from desktop.models.configuration import AppPaths
from desktop.services.configuration_service import ConfigurationService
from desktop.services.database_service import DatabaseService
from desktop.services.experiment_service import ExperimentService
from desktop.services.results_service import ResultsService
from federated.server import SUPPORTED_ALGORITHMS


class RuntimeController(QObject):
    snapshot_changed = Signal(dict)
    log_changed = Signal(str)
    status_changed = Signal(str)

    def __init__(self, paths: AppPaths, parent=None) -> None:
        super().__init__(parent)
        self.paths = paths
        self.configuration_service = ConfigurationService(paths.config_path, paths.project_root)
        self.results_service = ResultsService(paths.results_dir)
        self.database_service = DatabaseService(paths.database_path)
        self.experiment_service = ExperimentService(paths.project_root, paths.main_script)
        self.experiment_service.output_received.connect(self._append_log)
        self.experiment_service.status_changed.connect(self.status_changed.emit)
        self.experiment_service.finished.connect(self._handle_finished)
        self._logs: list[str] = []
        self._last_run_id: int | None = None

    def load_config(self, path: str | None = None) -> dict:
        return self.configuration_service.load(path)

    def supported_algorithms(self) -> tuple[str, ...]:
        return SUPPORTED_ALGORITHMS

    def build_runtime_config(self, base_config: dict, updates: dict) -> dict:
        return self.configuration_service.build_runtime_config(base_config, updates)

    def start_run(self, runtime_config: dict) -> str:
        results_dir = runtime_config["system"]["results_dir"]
        if not os.path.isabs(results_dir):
            results_dir = os.path.join(self.paths.project_root, results_dir)
        runtime_path = self.configuration_service.write_runtime_config(runtime_config, results_dir)
        self.results_service.set_results_dir(results_dir)
        self._logs.clear()
        self.log_changed.emit("")
        self.experiment_service.start(runtime_path, results_dir)
        self._last_run_id = self.database_service.create_run(
            {
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "status": "Running",
                "algorithm": runtime_config["algorithm"]["name"],
                "dataset": runtime_config["data"]["dataset"],
                "results_dir": results_dir,
                "runtime_config_path": runtime_path,
                "notes": "Triggered from PySide6 desktop dashboard.",
            }
        )
        self.publish_snapshot()
        return runtime_path

    def stop_run(self) -> None:
        self.experiment_service.stop()

    def append_manual_log(self, message: str) -> None:
        self._append_log(message.rstrip() + "\n")

    def publish_snapshot(self) -> None:
        metrics = self.results_service.load_metrics_snapshot()
        clients_frame = self.results_service.load_client_distribution()
        snapshot = {
            "metrics": metrics,
            "artifacts": self.results_service.discover_artifacts(),
            "summary": self.results_service.load_summary_text(),
            "runs": self.database_service.recent_runs(),
            "results_dir": self.results_service.results_dir,
            "logs": "".join(self._logs),
            "process_state": self.experiment_service.state,
            "clients_frame": clients_frame,
        }
        self.snapshot_changed.emit(snapshot)

    def _append_log(self, text: str) -> None:
        if not text:
            return
        self._logs.append(text)
        self.log_changed.emit("".join(self._logs))

    def _handle_finished(self, exit_code: int) -> None:
        if self._last_run_id is not None:
            self.database_service.finish_run(
                self._last_run_id,
                "Completed" if exit_code == 0 else "Failed",
                datetime.now().isoformat(timespec="seconds"),
                self.results_service.latest_summary_path(),
            )
        self.publish_snapshot()
