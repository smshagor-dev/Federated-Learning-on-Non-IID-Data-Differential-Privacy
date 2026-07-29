from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QTextEdit, QVBoxLayout, QWidget

from desktop.widgets.chart_card import ChartCard
from desktop.widgets.metric_card import MetricCard
from desktop.widgets.section_header import SectionHeader

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover
    pg = None


class PrivacyPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(
            SectionHeader(
                "Differential Privacy",
                "Privacy configuration, accountant status, and privacy-related charts for the active root runtime.",
            )
        )

        cards = QGridLayout()
        cards.setSpacing(10)
        self.enabled_card = MetricCard("Privacy Status", "--", "Root runtime privacy switch")
        self.epsilon_card = MetricCard("Epsilon", "--", "Latest finite privacy expenditure")
        self.noise_card = MetricCard("Noise Multiplier", "--", "Configured Gaussian noise scale")
        self.clip_card = MetricCard("Update Clip Norm", "--", "Configured client-update sensitivity bound")
        cards.addWidget(self.enabled_card, 0, 0)
        cards.addWidget(self.epsilon_card, 0, 1)
        cards.addWidget(self.noise_card, 0, 2)
        cards.addWidget(self.clip_card, 0, 3)
        layout.addLayout(cards)

        chart_grid = QGridLayout()
        chart_grid.setSpacing(10)
        self.epsilon_chart_card = ChartCard("Epsilon vs Rounds", "Finite epsilon values emitted by the active root runtime.")
        self.tradeoff_card = ChartCard("Privacy vs Utility", "Accuracy against finite epsilon values when available.")
        self.notes_card = ChartCard("Privacy Notes", "Important implementation details and boundary conditions.")
        self.epsilon_plot = self._build_plot()
        self.tradeoff_plot = self._build_plot()
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.epsilon_chart_card.set_content(self.epsilon_plot)
        self.tradeoff_card.set_content(self.tradeoff_plot)
        self.notes_card.set_content(self.notes)
        chart_grid.addWidget(self.epsilon_chart_card, 0, 0)
        chart_grid.addWidget(self.tradeoff_card, 0, 1)
        chart_grid.addWidget(self.notes_card, 1, 0, 1, 2)
        layout.addLayout(chart_grid)

    def _build_plot(self):
        if pg is None:
            label = QLabel("Install `pyqtgraph` to enable privacy charts.")
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

    def update_view(self, config: dict, metrics: dict) -> None:
        dp = config["dp"]
        epsilon_text = "--" if metrics["latest_epsilon"] is None else f"{metrics['latest_epsilon']:.4f}"
        self.enabled_card.set_value("Enabled" if dp["enabled"] else "Disabled")
        self.enabled_card.set_caption("Client-update clipping and Gaussian perturbation")
        self.epsilon_card.set_value(epsilon_text)
        self.epsilon_card.set_caption("No finite value when privacy is disabled")
        self.noise_card.set_value(str(dp["noise_multiplier"]))
        self.noise_card.set_caption(f"Target delta {dp['target_delta']}")
        self.clip_card.set_value(str(dp["update_clip_norm"]))
        self.clip_card.set_caption("Used only for client-update clipping in the central DP mechanism")
        self.notes.setPlainText(
            "\n".join(
                [
                    f"DP enabled: {dp['enabled']}",
                    f"Update clip norm (C): {dp['update_clip_norm']}",
                    f"Noise multiplier (sigma): {dp['noise_multiplier']}",
                    f"Target delta: {dp['target_delta']}",
                    f"Sampling strategy: {config['federated']['sampling_strategy']}",
                    f"Aggregation weighting: {config['federated']['aggregation_weighting']}",
                    "The active root runtime clips client updates locally, adds one Gaussian noise vector at the trusted server, and tracks client-level privacy with the Poisson-sampled moments accountant.",
                    "Opacus-backed sample-level accounting exists in the auxiliary fl_platform worker stack, not in this root desktop path.",
                ]
            )
        )
        if pg is not None:
            self.epsilon_plot.clear()
            self.tradeoff_plot.clear()
            for algorithm, series in metrics["series"].items():
                finite_eps = [(x, e, a) for x, e, a in zip(series["round"], series["epsilon"], series["test_acc"]) if e == e and e != float("inf")]
                if not finite_eps:
                    continue
                rounds = [item[0] for item in finite_eps]
                eps = [item[1] for item in finite_eps]
                acc = [item[2] * 100.0 for item in finite_eps]
                pen = pg.mkPen("#d97706", width=2)
                self.epsilon_plot.plot(rounds, eps, pen=pen)
                self.tradeoff_plot.plot(eps, acc, pen=pen)
