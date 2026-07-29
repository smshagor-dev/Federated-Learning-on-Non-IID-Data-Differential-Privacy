from __future__ import annotations

from PySide6.QtWidgets import QFrame, QVBoxLayout


class ResearchCard(QFrame):
    def __init__(self, parent=None, *, padding: tuple[int, int, int, int] = (14, 14, 14, 14)) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(*padding)
        self.layout.setSpacing(10)
