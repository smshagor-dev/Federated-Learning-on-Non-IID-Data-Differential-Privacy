from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QTextEdit, QPushButton, QVBoxLayout, QWidget

from desktop.widgets.metric_card import MetricCard
from desktop.widgets.research_card import ResearchCard
from desktop.widgets.searchable_table import SearchableTable
from desktop.widgets.section_header import SectionHeader


class ArtifactsPage(QWidget):
    open_artifact_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(
            SectionHeader(
                "Artifacts",
                "Generated files, metrics, plots, configs, and summaries attached to the active experiment run.",
            )
        )

        cards = QGridLayout()
        cards.setSpacing(10)
        self.count_card = MetricCard("Artifacts", "--", "Files discovered in the active results directory")
        self.type_card = MetricCard("Primary Type", "--", "Most common artifact family")
        self.latest_card = MetricCard("Latest File", "--", "Most recently modified artifact")
        self.size_card = MetricCard("Total Size", "--", "Approximate size across visible files")
        cards.addWidget(self.count_card, 0, 0)
        cards.addWidget(self.type_card, 0, 1)
        cards.addWidget(self.latest_card, 0, 2)
        cards.addWidget(self.size_card, 0, 3)
        layout.addLayout(cards)

        body = QHBoxLayout()
        body.setSpacing(10)
        self.table = SearchableTable(["Type", "Name", "Modified", "Size (KB)"])
        self.table.table.cellDoubleClicked.connect(self._open_current)
        body.addWidget(self.table, 3)

        self.preview_card = ResearchCard()
        self.preview_card.layout.addWidget(
            SectionHeader(
                "Artifact Notes",
                "Selection guidance for opening the right file first during result review.",
            )
        )
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_card.layout.addWidget(self.preview_text, 1)
        body.addWidget(self.preview_card, 2)
        layout.addLayout(body, 1)

        actions = QHBoxLayout()
        self.open_button = QPushButton("Open")
        self.open_button.clicked.connect(self._open_selected)
        self.preview_button = QPushButton("Preview")
        self.preview_button.setEnabled(False)
        self.reveal_button = QPushButton("Reveal in Folder")
        self.reveal_button.setEnabled(False)
        self.export_button = QPushButton("Export")
        self.export_button.setEnabled(False)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setEnabled(False)
        for button in [self.open_button, self.preview_button, self.reveal_button, self.export_button, self.delete_button]:
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self._paths: list[str] = []

    def load_artifacts(self, artifacts: list[dict[str, str]]) -> None:
        self._paths = [artifact["path"] for artifact in artifacts]
        rows = [[artifact["type"], artifact["name"], artifact["modified"], artifact["size_kb"]] for artifact in artifacts]
        if not rows:
            self.count_card.set_value("--")
            self.type_card.set_value("--")
            self.latest_card.set_value("--")
            self.size_card.set_value("--")
            self.preview_text.setPlainText("Run an experiment to populate the active results directory with metrics, plots, and summary artifacts.")
            self.table.set_rows([["--", "No artifacts", "--", "--"]])
            return

        type_counts: dict[str, int] = {}
        total_size = 0.0
        latest = artifacts[0]
        for artifact in artifacts:
            type_counts[artifact["type"]] = type_counts.get(artifact["type"], 0) + 1
            total_size += float(artifact["size_kb"])
            if artifact["modified"] > latest["modified"]:
                latest = artifact

        primary_type = max(type_counts.items(), key=lambda item: item[1])[0]
        self.count_card.set_value(str(len(artifacts)))
        self.type_card.set_value(primary_type)
        self.latest_card.set_value(latest["name"])
        self.size_card.set_value(f"{total_size:.1f} KB")
        self.preview_text.setPlainText(
            "\n".join(
                [
                    f"Latest artifact: {latest['name']}",
                    f"Most common file type: {primary_type}",
                    "Open summary.md first for the narrative overview.",
                    "Open CSV metrics and PNG plots next for detailed analysis.",
                    "Configuration YAML captures the exact reproducibility state for the run.",
                ]
            )
        )
        self.table.set_rows(rows)

    def _open_current(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._paths):
            self.open_artifact_requested.emit(self._paths[row])

    def _open_selected(self) -> None:
        row = self.table.table.currentRow()
        if 0 <= row < len(self._paths):
            self.open_artifact_requested.emit(self._paths[row])
