from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class SectionHeader(QWidget):
    def __init__(self, title: str, description: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.title_label = QLabel(title)
        self.title_label.setProperty("sectionTitle", True)
        self.description_label = QLabel(description)
        self.description_label.setProperty("muted", True)
        self.description_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        if description:
            layout.addWidget(self.description_label)

    def set_description(self, description: str) -> None:
        self.description_label.setText(description)
        self.description_label.setVisible(bool(description))
