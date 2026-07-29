from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from desktop.widgets.research_card import ResearchCard


class ChartCard(ResearchCard):
    def __init__(self, title: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent, padding=(14, 12, 14, 14))
        title_label = QLabel(title)
        title_label.setProperty("sectionTitle", True)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setProperty("muted", True)
        subtitle_label.setWordWrap(True)

        self.content_holder = QWidget()
        self.content_layout = QVBoxLayout(self.content_holder)
        self.content_layout.setContentsMargins(0, 4, 0, 0)
        self.content_layout.setSpacing(0)

        self.layout.addWidget(title_label)
        self.layout.addWidget(subtitle_label)
        self.layout.addWidget(self.content_holder, 1)

    def set_content(self, widget) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
        self.content_layout.addWidget(widget)
