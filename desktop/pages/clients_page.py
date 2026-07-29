from __future__ import annotations

import pandas as pd
from PySide6.QtWidgets import QGridLayout, QTextEdit, QVBoxLayout, QWidget

from desktop.widgets.metric_card import MetricCard
from desktop.widgets.research_card import ResearchCard
from desktop.widgets.searchable_table import SearchableTable
from desktop.widgets.section_header import SectionHeader


class ClientsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(
            SectionHeader(
                "Client Distribution",
                "Per-client sample counts and class-distribution data loaded from generated partition artifacts.",
            )
        )

        grid = QGridLayout()
        grid.setSpacing(10)
        self.client_count = MetricCard("Clients", "--", "Detected in client_distribution.csv")
        self.total_samples = MetricCard("Total Samples", "--", "Summed across visible client rows")
        self.min_samples = MetricCard("Minimum Client Size", "--", "Smallest partition size")
        self.max_samples = MetricCard("Maximum Client Size", "--", "Largest partition size")
        grid.addWidget(self.client_count, 0, 0)
        grid.addWidget(self.total_samples, 0, 1)
        grid.addWidget(self.min_samples, 0, 2)
        grid.addWidget(self.max_samples, 0, 3)
        layout.addLayout(grid)

        self.notes_card = ResearchCard()
        self.notes_card.layout.addWidget(
            SectionHeader(
                "Partition Insights",
                "Interpretation layer for non-IID balance, partition spread, and visible class coverage.",
            )
        )
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(140)
        self.notes_card.layout.addWidget(self.notes)
        layout.addWidget(self.notes_card)

        self.table = SearchableTable(["Client", "Samples", "Class Detail"])
        layout.addWidget(self.table, 1)

    def load_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            self.client_count.set_value("--")
            self.total_samples.set_value("--")
            self.min_samples.set_value("--")
            self.max_samples.set_value("--")
            self.notes.setPlainText("Run an experiment to generate client_distribution.csv and unlock client-level partition analysis.")
            self.table.set_rows([["--", "--", "Run an experiment to generate client_distribution.csv"]])
            return

        sample_counts = frame["sample_count"].tolist()
        self.client_count.set_value(str(len(frame.index)))
        self.total_samples.set_value(str(int(sum(sample_counts))))
        self.min_samples.set_value(str(int(min(sample_counts))))
        self.max_samples.set_value(str(int(max(sample_counts))))

        imbalance = max(sample_counts) / max(1, min(sample_counts))
        class_columns = [column for column in frame.columns if column.startswith("class_")]
        active_classes = [column.replace("class_", "") for column in class_columns if int(frame[column].sum()) > 0]
        self.notes.setPlainText(
            "\n".join(
                [
                    f"Detected {len(frame.index)} clients with {int(sum(sample_counts)):,} visible samples.",
                    f"Smallest partition: {int(min(sample_counts))} | Largest partition: {int(max(sample_counts))}.",
                    f"Imbalance ratio (max/min): {imbalance:.2f}.",
                    f"Visible active classes: {', '.join(active_classes[:12]) if active_classes else 'Unavailable'}",
                ]
            )
        )

        rows: list[list[str]] = []
        for row in frame.to_dict(orient="records"):
            detail = ", ".join(f"{column.replace('class_', 'c')}: {row[column]}" for column in class_columns)
            rows.append([str(row["client_id"]), str(row["sample_count"]), detail])
        self.table.set_rows(rows)
