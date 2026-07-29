from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout

from desktop.widgets.research_card import ResearchCard


class MetricCard(ResearchCard):
    def __init__(self, title: str, value: str = "--", caption: str = "", parent=None) -> None:
        super().__init__(parent, padding=(14, 12, 14, 12))
        self.setMinimumHeight(102)
        self.title_label = QLabel(title.upper())
        self.title_label.setProperty("metricLabel", True)
        self.value_label = QLabel(value)
        self.value_label.setProperty("metricValue", True)
        self.caption_label = QLabel(caption)
        self.caption_label.setProperty("muted", True)
        self.caption_label.setWordWrap(True)
        self.caption_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.layout.addWidget(self.title_label)
        self.layout.addWidget(self.value_label)
        self.layout.addWidget(self.caption_label)
        self.layout.addStretch(1)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)

    def set_caption(self, caption: str) -> None:
        self.caption_label.setText(caption)
