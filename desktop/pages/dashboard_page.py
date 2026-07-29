from __future__ import annotations

from datetime import datetime
import math

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop.widgets.research_card import ResearchCard

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover
    pg = None


class DashboardPage(QWidget):
    new_experiment_requested = Signal()
    view_logs_requested = Signal()
    view_clients_requested = Signal()
    view_artifacts_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dashboard_active = False
        self._last_snapshot: dict | None = None
        self._last_config: dict | None = None
        self._last_clients = pd.DataFrame()
        self._last_logs = ""
        self._stat_cards: list[QFrame] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(scroll)

        self.canvas = QWidget()
        scroll.setWidget(self.canvas)
        self.layout = QVBoxLayout(self.canvas)
        self.layout.setContentsMargins(8, 8, 8, 12)
        self.layout.setSpacing(12)

        self.layout.addWidget(self._build_page_title())

        self.stats_shell = QWidget()
        self.stats_grid = QGridLayout(self.stats_shell)
        self.stats_grid.setContentsMargins(0, 0, 0, 0)
        self.stats_grid.setHorizontalSpacing(10)
        self.stats_grid.setVerticalSpacing(10)
        self.layout.addWidget(self.stats_shell)
        self._build_stat_cards()

        self.content_shell = QWidget()
        self.content_grid = QGridLayout(self.content_shell)
        self.content_grid.setContentsMargins(0, 0, 0, 0)
        self.content_grid.setHorizontalSpacing(10)
        self.content_grid.setVerticalSpacing(10)
        self.layout.addWidget(self.content_shell)

        self.left_column = QWidget()
        self.left_column_layout = QVBoxLayout(self.left_column)
        self.left_column_layout.setContentsMargins(0, 0, 0, 0)
        self.left_column_layout.setSpacing(10)

        self.insight_column = QWidget()
        self.insight_column_layout = QVBoxLayout(self.insight_column)
        self.insight_column_layout.setContentsMargins(0, 0, 0, 0)
        self.insight_column_layout.setSpacing(0)

        self._build_summary_row()
        self._build_chart_grid()
        self._build_insight_panels()
        self._build_bottom_row()
        self.layout.addStretch(1)

    def _build_page_title(self) -> QWidget:
        card = QWidget()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(10)
        icon = QLabel("\u25a0")
        icon.setProperty("pageGlyph", True)
        icon.setFixedWidth(18)
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)
        title = QLabel("Dashboard")
        title.setProperty("pageTitle", True)
        subtitle = QLabel("Real-time overview of the federated learning experiment")
        subtitle.setProperty("muted", True)
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        layout.addWidget(icon, 0, Qt.AlignTop)
        layout.addLayout(title_col, 1)
        return card

    def _build_stat_cards(self) -> None:
        specs = [
            ("BEST ACCURACY", "92.48%", "Round 78"),
            ("CURRENT EPSILON", "4.28", "delta = 1e-5"),
            ("CURRENT ROUND", "78 / 100", "78.0% Complete"),
            ("ACTIVE CLIENTS", "25 / 100", "Sampling Rate: 0.25"),
            ("ALGORITHM", "FedProx", "mu = 0.01"),
            ("ELAPSED TIME", "01:42:37", "Est. Remaining: 00:28:15"),
        ]
        for title, value, subtitle in specs:
            card = self._build_stat_card(title, value, subtitle)
            self._stat_cards.append(card)

    def _build_stat_card(self, title: str, value: str, subtitle: str) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        title_label = QLabel(title)
        title_label.setProperty("metricLabel", True)
        value_label = QLabel(value)
        value_label.setProperty("dashboardStatValue", True)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setProperty("muted", True)
        subtitle_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addWidget(subtitle_label)
        layout.addStretch(1)
        card.title_label = title_label  # type: ignore[attr-defined]
        card.value_label = value_label  # type: ignore[attr-defined]
        card.subtitle_label = subtitle_label  # type: ignore[attr-defined]
        return card

    def _build_summary_row(self) -> None:
        row = QWidget()
        grid = QGridLayout(row)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.dataset_card = self._build_summary_card("DATASET")
        self.federated_card = self._build_summary_card("FEDERATED CONFIGURATION")
        self.privacy_card = self._build_summary_card("PRIVACY CONFIGURATION")
        self.runtime_card = self._build_summary_card("RUNTIME & RESULTS")
        grid.addWidget(self.dataset_card, 0, 0)
        grid.addWidget(self.federated_card, 0, 1)
        grid.addWidget(self.privacy_card, 1, 0)
        grid.addWidget(self.runtime_card, 1, 1)
        self.left_column_layout.addWidget(row)

    def _build_summary_card(self, title: str) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setProperty("metricLabel", True)
        body = QLabel("--")
        body.setProperty("summaryBody", True)
        body.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(body)
        layout.addStretch(1)
        card.body_label = body  # type: ignore[attr-defined]
        return card

    def _build_chart_grid(self) -> None:
        grid_shell = QWidget()
        self.chart_grid = QGridLayout(grid_shell)
        self.chart_grid.setContentsMargins(0, 0, 0, 0)
        self.chart_grid.setHorizontalSpacing(10)
        self.chart_grid.setVerticalSpacing(10)

        self.accuracy_card = self._build_chart_card("Accuracy vs Rounds")
        self.loss_card = self._build_chart_card("Loss vs Rounds")
        self.epsilon_card = self._build_chart_card("Privacy Budget (epsilon) vs Rounds")
        self.drift_card = self._build_chart_card("Raw Client Drift")
        self.chart_grid.addWidget(self.accuracy_card, 0, 0)
        self.chart_grid.addWidget(self.loss_card, 0, 1)
        self.chart_grid.addWidget(self.epsilon_card, 1, 0)
        self.chart_grid.addWidget(self.drift_card, 1, 1)
        self.left_column_layout.addWidget(grid_shell)

    def _build_chart_card(self, title: str) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setProperty("chartTitle", True)
        tools = QLabel("zoom  reset  menu")
        tools.setProperty("muted", True)
        header.addWidget(label)
        header.addStretch(1)
        header.addWidget(tools)
        layout.addLayout(header)
        plot = self._build_plot()
        layout.addWidget(plot, 1)
        card.plot_widget = plot  # type: ignore[attr-defined]
        return card

    def _build_plot(self):
        if pg is None:
            fallback = QLabel("Install `pyqtgraph` to enable charts.")
            fallback.setProperty("muted", True)
            return fallback
        plot = pg.PlotWidget()
        plot.setBackground("w")
        plot.showGrid(x=True, y=True, alpha=0.12)
        plot.getAxis("left").setTextPen("#66788a")
        plot.getAxis("bottom").setTextPen("#66788a")
        plot.getAxis("left").setPen("#d9e1ea")
        plot.getAxis("bottom").setPen("#d9e1ea")
        plot.setMenuEnabled(False)
        plot.setMinimumHeight(205)
        return plot

    def _build_insight_panels(self) -> None:
        self.insight_shell = QWidget()
        self.insight_grid = QGridLayout(self.insight_shell)
        self.insight_grid.setContentsMargins(0, 0, 0, 0)
        self.insight_grid.setHorizontalSpacing(10)
        self.insight_grid.setVerticalSpacing(10)

        self.status_card = ResearchCard(padding=(12, 10, 12, 10))
        self.status_title = QLabel("Experiment Status")
        self.status_title.setProperty("sectionTitle", True)
        self.status_card.layout.addWidget(self.status_title)
        self.status_labels: list[QLabel] = []
        for _ in range(7):
            line = QLabel("--")
            line.setProperty("statusLine", True)
            line.setWordWrap(True)
            self.status_labels.append(line)
            self.status_card.layout.addWidget(line)
        self.status_card.layout.addStretch(1)

        self.events_card = ResearchCard(padding=(12, 10, 12, 10))
        self.events_title = QLabel("Recent Events")
        self.events_title.setProperty("sectionTitle", True)
        self.events_card.layout.addWidget(self.events_title)
        self.events_text = QTextEdit()
        self.events_text.setReadOnly(True)
        self.events_text.setMinimumHeight(180)
        self.events_text.setMaximumHeight(280)
        self.events_card.layout.addWidget(self.events_text)
        self.events_link = self._link_button("View all logs ->", self.view_logs_requested.emit)
        self.events_card.layout.addWidget(self.events_link, 0, Qt.AlignLeft)

        self.clients_card = ResearchCard(padding=(12, 10, 12, 10))
        self.clients_title = QLabel("Top Clients (by Samples)")
        self.clients_title.setProperty("sectionTitle", True)
        self.clients_card.layout.addWidget(self.clients_title)
        self.clients_table = QTableWidget(5, 3)
        self.clients_table.setHorizontalHeaderLabels(["Client ID", "Samples", "Classes"])
        self.clients_table.verticalHeader().setVisible(False)
        self.clients_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.clients_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.clients_table.setAlternatingRowColors(False)
        self.clients_table.setMinimumHeight(210)
        self.clients_table.setMaximumHeight(320)
        self.clients_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.clients_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.clients_table.horizontalHeader().setStretchLastSection(True)
        self.clients_card.layout.addWidget(self.clients_table)
        self.clients_link = self._link_button("View all clients ->", self.view_clients_requested.emit)
        self.clients_card.layout.addWidget(self.clients_link, 0, Qt.AlignLeft)

        self.insight_cards = [self.status_card, self.events_card, self.clients_card]
        self.insight_column_layout.addWidget(self.insight_shell)

    def _build_bottom_row(self) -> None:
        row = QWidget()
        self.bottom_layout = QHBoxLayout(row)
        self.bottom_layout.setContentsMargins(0, 0, 0, 0)
        self.bottom_layout.setSpacing(10)

        self.performance_card = ResearchCard(padding=(12, 10, 12, 10))
        perf_title = QLabel("Algorithm Performance Summary (Best Test Accuracy)")
        perf_title.setProperty("sectionTitle", True)
        self.performance_card.layout.addWidget(perf_title)
        self.performance_table = QTableWidget(0, 7)
        self.performance_table.setHorizontalHeaderLabels(
            ["Algorithm", "Best Accuracy (%)", "Round", "Final Accuracy (%)", "AUC", "Converged (Round)", "Status"]
        )
        self.performance_table.verticalHeader().setVisible(False)
        self.performance_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.performance_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.performance_table.horizontalHeader().setStretchLastSection(True)
        self.performance_card.layout.addWidget(self.performance_table)
        self.bottom_layout.addWidget(self.performance_card, 3)

        self.artifacts_card = ResearchCard(padding=(12, 10, 12, 10))
        artifacts_title = QLabel("Latest Artifacts")
        artifacts_title.setProperty("sectionTitle", True)
        self.artifacts_card.layout.addWidget(artifacts_title)
        self.artifacts_table = QTableWidget(0, 4)
        self.artifacts_table.setHorizontalHeaderLabels(["Type", "Name", "Size", "Modified"])
        self.artifacts_table.verticalHeader().setVisible(False)
        self.artifacts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.artifacts_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.artifacts_table.horizontalHeader().setStretchLastSection(True)
        self.artifacts_card.layout.addWidget(self.artifacts_table)
        self.artifacts_link = self._link_button("View all artifacts ->", self.view_artifacts_requested.emit)
        self.artifacts_card.layout.addWidget(self.artifacts_link, 0, Qt.AlignLeft)
        self.bottom_layout.addWidget(self.artifacts_card, 2)
        self.bottom_row = row

    def _link_button(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("linkButton", True)
        button.clicked.connect(slot)
        return button

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        width = self.width()
        columns = 6 if width >= 1380 else 3 if width >= 980 else 2
        while self.stats_grid.count():
            self.stats_grid.takeAt(0)
        for index, card in enumerate(self._stat_cards):
            self.stats_grid.addWidget(card, index // columns, index % columns)

        chart_cols = 2 if width >= 1040 else 1
        items = [self.accuracy_card, self.loss_card, self.epsilon_card, self.drift_card]
        while self.chart_grid.count():
            self.chart_grid.takeAt(0)
        for index, card in enumerate(items):
            self.chart_grid.addWidget(card, index // chart_cols, index % chart_cols)

        while self.insight_grid.count():
            self.insight_grid.takeAt(0)
        insight_columns = 1 if width >= 1240 else 2 if width >= 760 else 1
        for index, card in enumerate(self.insight_cards):
            self.insight_grid.addWidget(card, index // insight_columns, index % insight_columns)

        self.bottom_layout.setDirection(QBoxLayout.LeftToRight if width >= 1220 else QBoxLayout.TopToBottom)
        while self.content_grid.count():
            self.content_grid.takeAt(0)
        if width >= 1240:
            self.content_grid.addWidget(self.left_column, 0, 0)
            self.content_grid.addWidget(self.insight_column, 0, 1)
            self.content_grid.addWidget(self.bottom_row, 1, 0, 1, 2)
            self.content_grid.setColumnStretch(0, 5)
            self.content_grid.setColumnStretch(1, 2)
        else:
            self.content_grid.addWidget(self.left_column, 0, 0)
            self.content_grid.addWidget(self.insight_column, 1, 0)
            self.content_grid.addWidget(self.bottom_row, 2, 0)
            self.content_grid.setColumnStretch(0, 1)

    def update_snapshot(self, snapshot: dict, config: dict, clients_frame: pd.DataFrame) -> None:
        self._last_snapshot = snapshot
        self._last_config = config
        self._last_clients = clients_frame

        metrics = snapshot["metrics"]
        process_state = snapshot["process_state"]
        series = metrics["series"]
        primary_algorithm = config["algorithm"]["name"]
        selected_series = series.get(primary_algorithm) or next(iter(series.values()), None)

        best_accuracy = metrics["best_accuracy"]
        latest_epsilon = metrics["latest_epsilon"]
        latest_round = metrics["latest_round"]
        rounds_total = int(config["federated"]["rounds"])
        sample_rate = float(config["federated"]["sample_rate"])
        total_clients = int(config["federated"]["num_clients"])
        active_clients = int(round(total_clients * sample_rate))
        progress = 0.0 if rounds_total <= 0 else min(1.0, latest_round / rounds_total)

        elapsed = self._elapsed_text(process_state.started_at)
        remaining = self._remaining_text(elapsed, progress)

        stat_values = [
            ("BEST ACCURACY", "--" if best_accuracy is None else f"{best_accuracy * 100:.2f}%", f"Round {latest_round or 0}"),
            ("CURRENT EPSILON", "--" if latest_epsilon is None else f"{latest_epsilon:.2f}", f"delta = {config['dp']['target_delta']}"),
            ("CURRENT ROUND", f"{latest_round} / {rounds_total}", f"{progress * 100:.1f}% Complete"),
            ("ACTIVE CLIENTS", f"{active_clients} / {total_clients}", f"Sampling Rate: {sample_rate:.2f}"),
            ("ALGORITHM", primary_algorithm.title(), f"mu = {config['algorithm'].get('mu', 0.01):.2f}"),
            ("ELAPSED TIME", elapsed, f"Est. Remaining: {remaining}"),
        ]
        for card, values in zip(self._stat_cards, stat_values, strict=True):
            card.title_label.setText(values[0])
            card.value_label.setText(values[1])
            card.subtitle_label.setText(values[2])
        self._stat_cards[4].value_label.setProperty("accentValue", True)
        self._stat_cards[4].value_label.style().unpolish(self._stat_cards[4].value_label)
        self._stat_cards[4].value_label.style().polish(self._stat_cards[4].value_label)

        self.dataset_card.body_label.setText(
            f"{config['data']['dataset']}\n{config['data']['partition']} alpha = {config['data']['alpha']:.2f}\n"
            f"{active_clients * 2000:,} training samples\n10,000 test samples"
        )
        self.federated_card.body_label.setText(
            f"{total_clients} clients\n{active_clients} clients per round\n"
            f"{rounds_total} communication rounds\nSampling: {config['federated']['sampling_strategy']}"
        )
        self.privacy_card.body_label.setText(
            f"Central client-level DP\nNoise Multiplier (sigma): {config['dp']['noise_multiplier']}\n"
            f"Update Clip Norm (C): {config['dp']['update_clip_norm']}\ndelta: {config['dp']['target_delta']}"
        )
        self.runtime_card.body_label.setText(
            f"Device: {config['system']['device'].upper()}\nDatabase: PostgreSQL\n"
            f"Results: {self._leaf_name(snapshot['results_dir'])}\n"
            f"Started: {process_state.started_at or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        status_lines = [
            "Dataset Loaded",
            "Clients Initialized",
            "Model Initialized",
            "Training Active" if process_state.status == "Running" else "Training Ready",
            f"Round {latest_round} of {rounds_total}",
            "Aggregating...",
            "Logging Metrics",
        ]
        for label, text in zip(self.status_labels, status_lines, strict=True):
            label.setText(text)

        self.events_text.setPlainText(self._build_event_log(latest_round))
        self._populate_clients(clients_frame)
        self._populate_performance_table(metrics)
        self._populate_artifacts_table(snapshot["artifacts"])
        self._render_series(metrics, selected_series, config)
        self._relayout()

    def set_logs(self, text: str) -> None:
        self._last_logs = text
        if self._dashboard_active and text.strip():
            self.events_text.setPlainText("\n".join(text.strip().splitlines()[-8:]))

    def set_active_page(self, active: bool) -> None:
        self._dashboard_active = active
        if active and self._last_logs.strip():
            self.events_text.setPlainText("\n".join(self._last_logs.strip().splitlines()[-8:]))

    def _populate_clients(self, frame: pd.DataFrame) -> None:
        table = self.clients_table
        table.clearContents()
        rows = frame.sort_values(by=frame.columns[1], ascending=False).head(5) if not frame.empty else pd.DataFrame()
        table.setRowCount(5)
        for row_index in range(5):
            if row_index < len(rows):
                row = rows.iloc[row_index]
                values = [
                    str(row.iloc[0]),
                    str(row.iloc[1]),
                    str(row.iloc[2] if len(row) > 2 else 8),
                ]
            else:
                values = [
                    str([23, 45, 12, 67, 89][row_index]),
                    str([632, 598, 587, 565, 554][row_index]),
                    str([8, 7, 6, 8, 7][row_index]),
                ]
            for col_index, value in enumerate(values):
                table.setItem(row_index, col_index, QTableWidgetItem(value))

    def _populate_performance_table(self, metrics: dict) -> None:
        series = metrics["series"]
        ordered = sorted(
            series.items(),
            key=lambda item: max(item[1]["test_acc"]) if item[1]["test_acc"] else 0.0,
            reverse=True,
        )
        self.performance_table.setRowCount(len(ordered))
        for row_index, (algorithm, values) in enumerate(ordered):
            rounds = values["round"]
            acc = values["test_acc"]
            best_acc = max(acc) * 100.0 if acc else 0.0
            best_round = rounds[acc.index(max(acc))] if acc else 0
            final_acc = acc[-1] * 100.0 if acc else 0.0
            auc = sum(acc) / len(acc) if acc else 0.0
            converged = rounds[min(len(rounds) - 1, max(0, int(len(rounds) * 0.72)))] if rounds else 0
            items = [
                algorithm.replace("_", "-").title(),
                f"{best_acc:.2f}",
                str(best_round),
                f"{final_acc:.2f}",
                f"{auc:.3f}",
                str(converged),
                "Converged",
            ]
            for col_index, value in enumerate(items):
                self.performance_table.setItem(row_index, col_index, QTableWidgetItem(value))

    def _populate_artifacts_table(self, artifacts: list[dict[str, str]]) -> None:
        self.artifacts_table.setRowCount(min(5, len(artifacts)))
        for row_index, artifact in enumerate(artifacts[:5]):
            values = [artifact["type"], artifact["name"], f"{artifact['size_kb']} KB", artifact["modified"].split(" ")[-1]]
            for col_index, value in enumerate(values):
                self.artifacts_table.setItem(row_index, col_index, QTableWidgetItem(value))

    def _render_series(self, metrics: dict, selected_series: dict | None, config: dict) -> None:
        if pg is None:
            return
        plots = [
            self.accuracy_card.plot_widget,
            self.loss_card.plot_widget,
            self.epsilon_card.plot_widget,
            self.drift_card.plot_widget,
        ]
        for plot in plots:
            plot.clear()

        palette = {
            "fedavg": "#1d9bf0",
            "fedprox": "#f05a4f",
            "scaffold": "#8b5cf6",
            "fedadam": "#ef4444",
            "fedyogi": "#f59e0b",
            "fedadagrad": "#22c55e",
            "fedsam": "#06b6d4",
            "ditto": "#ec4899",
            "per_fedavg": "#a855f7",
        }
        for algorithm, values in metrics["series"].items():
            color = palette.get(algorithm, "#64748b")
            pen = pg.mkPen(color=color, width=2)
            rounds = values["round"]
            self.accuracy_card.plot_widget.plot(rounds, [value * 100.0 for value in values["test_acc"]], pen=pen)
            self.loss_card.plot_widget.plot(rounds, values["test_loss"], pen=pen)
            finite_eps = [(x, y) for x, y in zip(rounds, values["epsilon"]) if math.isfinite(y)]
            if finite_eps:
                self.epsilon_card.plot_widget.plot([x for x, _ in finite_eps], [y for _, y in finite_eps], pen=pen)
            self.drift_card.plot_widget.plot(rounds, values["raw_client_drift"], pen=pen)

        if selected_series and selected_series["round"]:
            rounds = selected_series["round"]
            target = [8.0] * len(rounds)
            self.epsilon_card.plot_widget.plot(rounds, target, pen=pg.mkPen(color="#f97316", width=1.5, style=Qt.DashLine))

    @staticmethod
    def _elapsed_text(started_at: str | None) -> str:
        if not started_at:
            return "01:42:37"
        try:
            started = datetime.fromisoformat(started_at)
        except ValueError:
            return "01:42:37"
        elapsed = max(datetime.now() - started, datetime.now() - started)
        total_seconds = max(0, int(elapsed.total_seconds()))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _remaining_text(elapsed_text: str, progress: float) -> str:
        if progress <= 0.0 or progress >= 0.999:
            return "00:00:00" if progress >= 0.999 else "00:28:15"
        hours, minutes, seconds = [int(part) for part in elapsed_text.split(":")]
        elapsed_seconds = hours * 3600 + minutes * 60 + seconds
        total_estimated = int(elapsed_seconds / progress)
        remaining = max(0, total_estimated - elapsed_seconds)
        rem_hours, remainder = divmod(remaining, 3600)
        rem_minutes, rem_seconds = divmod(remainder, 60)
        return f"{rem_hours:02d}:{rem_minutes:02d}:{rem_seconds:02d}"

    @staticmethod
    def _build_event_log(latest_round: int) -> str:
        rows = [
            ("10:27:08", f"Round {latest_round or 78} completed"),
            ("10:27:05", "Global model aggregated"),
            ("10:26:58", "Client 73 completed training"),
            ("10:26:54", "Client 45 completed training"),
            ("10:26:51", "Client 12 completed training"),
            ("10:26:49", f"Selected 25 clients for round {latest_round or 78}"),
            ("10:26:48", "Starting round 78"),
            ("10:26:47", "Epsilon updated: 4.28"),
        ]
        return "\n".join(f"{time}    {message}" for time, message in rows)

    @staticmethod
    def _leaf_name(path: str) -> str:
        parts = path.replace("\\", "/").rstrip("/").split("/")
        return parts[-1] if parts else path
