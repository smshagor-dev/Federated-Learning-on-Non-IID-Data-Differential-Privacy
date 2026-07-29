from __future__ import annotations

from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget


class RuntimeProgress(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.status = QLabel("Studio ready")
        self.status.setProperty("muted", True)
        layout.addWidget(self.bar)
        layout.addWidget(self.status)

    def set_busy(self, busy: bool) -> None:
        if busy:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(0)

    def set_status(self, text: str) -> None:
        self.status.setText(text)
