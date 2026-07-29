from __future__ import annotations

from PySide6.QtWidgets import QFormLayout

from desktop.widgets.research_card import ResearchCard
from desktop.widgets.section_header import SectionHeader


class ConfigurationGroup(ResearchCard):
    def __init__(self, title: str, description: str = "", parent=None) -> None:
        super().__init__(parent)
        self.header = SectionHeader(title, description)
        self.form = QFormLayout()
        self.form.setContentsMargins(0, 2, 0, 0)
        self.form.setHorizontalSpacing(16)
        self.form.setVerticalSpacing(8)
        self.form.setLabelAlignment(self.form.labelAlignment())
        self.layout.addWidget(self.header)
        self.layout.addLayout(self.form)

    def add_row(self, label: str, widget) -> None:
        self.form.addRow(label, widget)
