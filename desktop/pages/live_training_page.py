from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from desktop.widgets.chart_card import ChartCard
from desktop.widgets.log_viewer import LogViewer
from desktop.widgets.metric_card import MetricCard
from desktop.widgets.runtime_progress import RuntimeProgress
from desktop.widgets.section_header import SectionHeader

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover
    pg = None


class LiveTrainingPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(
            SectionHeader(
                "Live Training",
                "Runtime-oriented page for current process state, live metrics, charts, and log output.",
            )
        )

        self.progress = RuntimeProgress()
        layout.addWidget(self.progress)

        top = QGridLayout()
        top.setSpacing(10)
        self.status_card = MetricCard("Experiment State", "Idle", "No active run")
        self.round_card = MetricCard("Current Round", "--", "Updated from latest CSV row")
        self.pid_card = MetricCard("Process ID", "--", "Local managed child process")
        self.results_card = MetricCard("Results Directory", "--", "Active artifact root")
        top.addWidget(self.status_card, 0, 0)
        top.addWidget(self.round_card, 0, 1)
        top.addWidget(self.pid_card, 0, 2)
        top.addWidget(self.results_card, 0, 3)
        layout.addLayout(top)

        mid = QHBoxLayout()
        mid.setSpacing(10)
        self.command_card = ChartCard("Active Command", "Exact command preview sent to the managed CLI runtime.")
        self.command_label = QLabel("Command preview will appear here.")
        self.command_label.setWordWrap(True)
        self.command_label.setProperty("muted", True)
        self.command_card.set_content(self.command_label)
        self.accuracy_card = ChartCard("Accuracy Preview", "Latest accuracy trend during the active results session.")
        self.accuracy_plot = self._build_plot()
        self.accuracy_card.set_content(self.accuracy_plot)
        mid.addWidget(self.command_card, 2)
        mid.addWidget(self.accuracy_card, 3)
        layout.addLayout(mid)

        self.log_card = ChartCard("Live Console", "Merged standard output from the running CLI experiment.")
        self.log_viewer = LogViewer()
        self.log_card.set_content(self.log_viewer)
        layout.addWidget(self.log_card, 1)

    def _build_plot(self):
        if pg is None:
            label = QLabel("Install `pyqtgraph` to enable preview charts.")
            label.setProperty("muted", True)
            return label
        plot = pg.PlotWidget()
        plot.setBackground("w")
        plot.showGrid(x=True, y=True, alpha=0.18)
        plot.getAxis("left").setTextPen("#607284")
        plot.getAxis("bottom").setTextPen("#607284")
        plot.getAxis("left").setPen("#d7e0ea")
        plot.getAxis("bottom").setPen("#d7e0ea")
        plot.setMinimumHeight(220)
        return plot

    def update_process_state(self, state, metrics: dict) -> None:
        self.status_card.set_value(state.status)
        self.status_card.set_caption(state.started_at or "Awaiting experiment launch")
        self.round_card.set_value(str(metrics["latest_round"]))
        self.round_card.set_caption("Most recent completed communication round")
        self.pid_card.set_value(str(state.process_id or "--"))
        self.pid_card.set_caption(state.finished_at or "Process active")
        self.results_card.set_value(state.results_dir)
        self.results_card.set_caption("Desktop-managed output directory")
        self.command_label.setText(state.command_preview or "Command preview will appear here.")
        self.progress.set_busy(state.status in {"Running", "Stopping"})
        self.progress.set_status(f"{state.status} | {state.results_dir}")
        if pg is not None:
            self.accuracy_plot.clear()
            for series in metrics["series"].values():
                self.accuracy_plot.plot(series["round"], [value * 100.0 for value in series["test_acc"]], pen=pg.mkPen("#0f766e", width=2))

    def set_logs(self, text: str) -> None:
        self.log_viewer.set_text(text)
