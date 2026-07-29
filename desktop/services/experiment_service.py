from __future__ import annotations

import os
import sys
from datetime import datetime

from PySide6.QtCore import QObject, QProcess, Signal

from desktop.models.experiment_state import ExperimentState


class ExperimentService(QObject):
    output_received = Signal(str)
    status_changed = Signal(str)
    finished = Signal(int)

    def __init__(self, project_root: str, main_script: str, parent=None) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.main_script = main_script
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._handle_output)
        self.process.finished.connect(self._handle_finished)
        self.state = ExperimentState(results_dir=os.path.join(project_root, "results"))

    def build_command(self, runtime_config_path: str) -> list[str]:
        return [sys.executable, self.main_script, "--cli", "--config", runtime_config_path]

    def start(self, runtime_config_path: str, results_dir: str) -> None:
        if self.process.state() != QProcess.NotRunning:
            raise RuntimeError("An experiment is already running.")
        command = self.build_command(runtime_config_path)
        self.state = ExperimentState(
            status="Running",
            process_id=None,
            started_at=datetime.now().isoformat(timespec="seconds"),
            runtime_config_path=runtime_config_path,
            results_dir=results_dir,
            command_preview=" ".join(command),
        )
        self.process.setWorkingDirectory(self.project_root)
        self.process.start(command[0], command[1:])
        self.status_changed.emit(self.state.status)

    def stop(self) -> None:
        if self.process.state() == QProcess.NotRunning:
            return
        self.state.status = "Stopping"
        self.status_changed.emit(self.state.status)
        self.process.terminate()

    def _handle_output(self) -> None:
        chunk = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if self.process.processId():
            self.state.process_id = int(self.process.processId())
        if chunk:
            self.output_received.emit(chunk)

    def _handle_finished(self, exit_code: int) -> None:
        self.state.status = "Completed" if exit_code == 0 else "Failed"
        self.state.finished_at = datetime.now().isoformat(timespec="seconds")
        self.status_changed.emit(self.state.status)
        self.finished.emit(exit_code)
