from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class EmptyState(QWidget):
    def __init__(self, title: str, detail: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(6)
        self.title = QLabel(title)
        self.title.setProperty("sectionTitle", True)
        self.title.setAlignment(Qt.AlignCenter)
        self.detail = QLabel(detail)
        self.detail.setProperty("muted", True)
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        layout.addStretch(1)
