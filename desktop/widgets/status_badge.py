from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


class StatusBadge(QLabel):
    _COLORS = {
        "Idle": ("#eef3f8", "#607284"),
        "Running": ("#0f766e", "#ffffff"),
        "Completed": ("#0b8f55", "#ffffff"),
        "Failed": ("#c2410c", "#ffffff"),
        "Stopping": ("#d97706", "#ffffff"),
        "SQLite": ("#eef3f8", "#16202b"),
        "CPU": ("#eef3f8", "#16202b"),
        "CUDA": ("#0f766e", "#ffffff"),
    }

    def __init__(self, text: str = "Idle", parent=None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(26)
        self.set_status(text)

    def set_status(self, text: str) -> None:
        background, foreground = self._COLORS.get(text, ("#eef3f8", "#16202b"))
        self.setText(text)
        self.setStyleSheet(
            f"background: {background}; color: {foreground}; border-radius: 12px; padding: 4px 10px; font-size: 11px; font-weight: 600;"
        )
