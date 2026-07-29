from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget


class LogViewer(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search logs")
        top.addWidget(self.search)
        layout.addLayout(top)
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setProperty("logViewer", True)
        layout.addWidget(self.editor, 1)
        self._full_text = ""
        self.search.textChanged.connect(self._apply_filter)

    def set_text(self, text: str) -> None:
        self._full_text = text
        self._apply_filter(self.search.text())

    def _apply_filter(self, needle: str) -> None:
        if not needle:
            visible = self._full_text
        else:
            lines = [line for line in self._full_text.splitlines() if needle.lower() in line.lower()]
            visible = "\n".join(lines)
        self.editor.setPlainText(visible)
        cursor = self.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)
