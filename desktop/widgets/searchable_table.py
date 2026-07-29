from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget


class SearchableTable(QWidget):
    def __init__(self, headers: list[str], parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search table")
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search)
        layout.addLayout(top)

        self.table = QTableWidget()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(False)
        layout.addWidget(self.table, 1)

    def set_rows(self, rows: list[list[str]]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for col_index, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                self.table.setItem(row_index, col_index, item)
        self.table.setSortingEnabled(True)
        self._apply_filter(self.search.text())

    def _apply_filter(self, text: str) -> None:
        needle = text.lower().strip()
        for row in range(self.table.rowCount()):
            visible = False
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item is not None and needle in item.text().lower():
                    visible = True
                    break
            self.table.setRowHidden(row, bool(needle) and not visible)
