from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from desktop.widgets.chart_card import ChartCard
from desktop.widgets.section_header import SectionHeader

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover
    pg = None


class MetricsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(
            SectionHeader(
                "Metrics and Charts",
                "Detailed live chart workspace using the same compact white-card structure as the original dashboard.",
            )
        )
        grid = QGridLayout()
        grid.setSpacing(10)
        self.cards = {
            "accuracy": ChartCard("Accuracy vs Rounds", "Global accuracy by algorithm."),
            "loss": ChartCard("Loss vs Rounds", "Test loss by algorithm."),
            "privacy": ChartCard("Privacy Budget", "Finite epsilon values where available."),
            "drift": ChartCard("Raw Client Drift", "Mean deviation of raw client updates from the cohort-average raw update."),
            "clipped_drift": ChartCard("Clipped Client Drift", "Mean deviation after client-update clipping and before server noise."),
            "variance": ChartCard("Weight Variance", "Mean parameter variance across client local states."),
        }
        self.plots = {name: self._build_plot() for name in self.cards}
        self.cards["accuracy"].set_content(self.plots["accuracy"])
        self.cards["loss"].set_content(self.plots["loss"])
        self.cards["privacy"].set_content(self.plots["privacy"])
        self.cards["drift"].set_content(self.plots["drift"])
        self.cards["clipped_drift"].set_content(self.plots["clipped_drift"])
        self.cards["variance"].set_content(self.plots["variance"])
        grid.addWidget(self.cards["accuracy"], 0, 0)
        grid.addWidget(self.cards["loss"], 0, 1)
        grid.addWidget(self.cards["privacy"], 1, 0)
        grid.addWidget(self.cards["drift"], 1, 1)
        grid.addWidget(self.cards["clipped_drift"], 2, 0)
        grid.addWidget(self.cards["variance"], 2, 1)
        layout.addLayout(grid)

    def _build_plot(self):
        if pg is None:
            label = QLabel("Install `pyqtgraph` to enable live charts.")
            label.setProperty("muted", True)
            return label
        plot = pg.PlotWidget()
        plot.setBackground("w")
        plot.showGrid(x=True, y=True, alpha=0.18)
        plot.getAxis("left").setTextPen("#607284")
        plot.getAxis("bottom").setTextPen("#607284")
        plot.getAxis("left").setPen("#d7e0ea")
        plot.getAxis("bottom").setPen("#d7e0ea")
        plot.setMinimumHeight(210)
        return plot

    def update_snapshot(self, metrics: dict) -> None:
        if pg is None:
            return
        palette = {
            "fedavg": "#2563eb",
            "fedprox": "#dc2626",
            "scaffold": "#0891b2",
            "fedadam": "#7c3aed",
            "fedyogi": "#d97706",
            "fedadagrad": "#0b8f55",
            "fedsam": "#4f46e5",
            "ditto": "#8b5e3c",
            "per_fedavg": "#64748b",
        }
        for plot in self.plots.values():
            plot.clear()
        for algorithm, series in metrics["series"].items():
            pen = pg.mkPen(color=palette.get(algorithm, "#475569"), width=2)
            rounds = series["round"]
            self.plots["accuracy"].plot(rounds, [value * 100.0 for value in series["test_acc"]], pen=pen)
            self.plots["loss"].plot(rounds, series["test_loss"], pen=pen)
            finite_eps = [(x, y) for x, y in zip(rounds, series["epsilon"]) if y == y and y != float("inf")]
            if finite_eps:
                self.plots["privacy"].plot([item[0] for item in finite_eps], [item[1] for item in finite_eps], pen=pen)
            self.plots["drift"].plot(rounds, series["raw_client_drift"], pen=pen)
            self.plots["clipped_drift"].plot(rounds, series["clipped_client_drift"], pen=pen)
            self.plots["variance"].plot(rounds, series["weight_variance"], pen=pen)
