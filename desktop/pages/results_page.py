from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop.widgets.metric_card import MetricCard
from desktop.widgets.research_card import ResearchCard
from desktop.widgets.section_header import SectionHeader


class ResultsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(scroll)

        canvas = QWidget()
        scroll.setWidget(canvas)
        self.layout = QVBoxLayout(canvas)
        self.layout.setContentsMargins(0, 0, 0, 8)
        self.layout.setSpacing(12)
        self.layout.addWidget(
            SectionHeader(
                "Experiment Results",
                "Human-readable summary, extracted findings, and interpretation notes generated from completed runs.",
            )
        )

        self.cards_shell = QWidget()
        self.cards_grid = QGridLayout(self.cards_shell)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setHorizontalSpacing(10)
        self.cards_grid.setVerticalSpacing(10)
        self.layout.addWidget(self.cards_shell)

        self.metric_cards = [
            MetricCard("Summary Status", "--", "Whether a results summary is currently available"),
            MetricCard("Summary Lines", "--", "Number of visible lines in summary.md"),
            MetricCard("Accuracy Mention", "--", "Whether the summary discusses accuracy"),
            MetricCard("Privacy Mention", "--", "Whether the summary discusses privacy"),
        ]
        self.summary_card_metric = self.metric_cards[0]
        self.line_count_card = self.metric_cards[1]
        self.accuracy_card = self.metric_cards[2]
        self.privacy_card = self.metric_cards[3]

        self.body_shell = QWidget()
        self.body_layout = QHBoxLayout(self.body_shell)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(10)

        self.summary_card = ResearchCard(padding=(16, 14, 16, 16))
        self.summary_card.layout.addWidget(
            SectionHeader("Markdown Summary", "Formatted research report view of the generated summary artifact.")
        )
        self.summary_meta = QLabel("Rendered from `summary.md`")
        self.summary_meta.setProperty("muted", True)
        self.summary_card.layout.addWidget(self.summary_meta)
        self.summary = QTextBrowser()
        self.summary.setOpenExternalLinks(False)
        self.summary.setReadOnly(True)
        self.summary.setMinimumHeight(360)
        self.summary.document().setDocumentMargin(18)
        self.summary.setStyleSheet(self._summary_stylesheet())
        self.summary_card.layout.addWidget(self.summary, 1)

        self.notes_card = ResearchCard()
        self.notes_card.layout.addWidget(
            SectionHeader("Interpretation Notes", "Context for reading the generated output and validating the run.")
        )
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMinimumHeight(360)
        self.notes_card.layout.addWidget(self.notes, 1)

        self.body_layout.addWidget(self.summary_card, 3)
        self.body_layout.addWidget(self.notes_card, 2)
        self.layout.addWidget(self.body_shell, 1)
        self._relayout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        width = self.width()

        while self.cards_grid.count():
            self.cards_grid.takeAt(0)
        columns = 4 if width >= 1320 else 2 if width >= 760 else 1
        for index, card in enumerate(self.metric_cards):
            self.cards_grid.addWidget(card, index // columns, index % columns)

        while self.body_layout.count():
            self.body_layout.takeAt(0)
        if width >= 1040:
            self.body_layout.addWidget(self.summary_card, 3)
            self.body_layout.addWidget(self.notes_card, 2)
        else:
            stacked = QVBoxLayout()
            stacked.setContentsMargins(0, 0, 0, 0)
            stacked.setSpacing(10)
            stacked.addWidget(self.summary_card)
            stacked.addWidget(self.notes_card)
            self.body_layout.addLayout(stacked, 1)

    def set_summary(self, text: str) -> None:
        stripped = text.strip()
        lines = [line for line in stripped.splitlines() if line.strip()] if stripped else []
        lowered = stripped.lower()
        self.summary_card_metric.set_value("Available" if stripped else "Missing")
        self.summary_card_metric.set_caption("summary.md loaded from the active results directory")
        self.line_count_card.set_value(str(len(lines)))
        self.line_count_card.set_caption("Non-empty lines extracted from summary text")
        self.accuracy_card.set_value("Yes" if "accuracy" in lowered or "acc" in lowered else "No")
        self.accuracy_card.set_caption("Keyword scan across generated summary content")
        self.privacy_card.set_value("Yes" if "privacy" in lowered or "epsilon" in lowered else "No")
        self.privacy_card.set_caption("Keyword scan across generated summary content")

        summary_text = text or "# No Summary Yet\n\nRun an experiment to generate `summary.md`."
        self.summary.setMarkdown(summary_text)
        self.summary_meta.setText(
            f"Rendered from summary.md | {len(lines)} non-empty lines | {'privacy-aware' if 'privacy' in lowered or 'epsilon' in lowered else 'standard'} report"
        )
        self.notes.setPlainText(
            "\n".join(
                [
                    "Best accuracy and final accuracy come directly from generated run CSV files.",
                    "Finite epsilon values appear only when privacy is enabled in the active root runtime.",
                    "Client drift and weight variance summarize disagreement among local client states or updates.",
                    "Use this page as the narrative layer, then cross-check metrics and artifacts for exact numeric validation.",
                ]
            )
        )

    @staticmethod
    def _summary_stylesheet() -> str:
        return (
            "QTextBrowser {"
            "background: #fcfdff;"
            "border: 1px solid #dfe7ef;"
            "border-radius: 12px;"
            "padding: 6px;"
            "color: #223046;"
            "font-family: 'Segoe UI';"
            "font-size: 13px;"
            "line-height: 1.55;"
            "}"
            "QScrollBar:vertical { width: 10px; }"
        )
