from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QTextEdit, QVBoxLayout, QWidget

from desktop.widgets.log_viewer import LogViewer
from desktop.widgets.metric_card import MetricCard
from desktop.widgets.research_card import ResearchCard
from desktop.widgets.section_header import SectionHeader


class LogsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(
            SectionHeader(
                "Runtime Logs",
                "Monospace runtime output with filtering, status summaries, and quick triage guidance.",
            )
        )

        cards = QGridLayout()
        cards.setSpacing(10)
        self.lines_card = MetricCard("Log Lines", "--", "Visible line count in the merged runtime console")
        self.last_level_card = MetricCard("Last Signal", "--", "Best-effort classification from the newest line")
        self.last_line_card = MetricCard("Latest Entry", "--", "Newest visible console line")
        cards.addWidget(self.lines_card, 0, 0)
        cards.addWidget(self.last_level_card, 0, 1)
        cards.addWidget(self.last_line_card, 0, 2)
        layout.addLayout(cards)

        self.viewer = LogViewer()
        layout.addWidget(self.viewer, 1)

        self.notes_card = ResearchCard()
        self.notes_card.layout.addWidget(
            SectionHeader(
                "Triage Notes",
                "Quick reading of the current console stream so debugging can start from the right point.",
            )
        )
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(120)
        self.notes_card.layout.addWidget(self.notes)
        layout.addWidget(self.notes_card)

    def set_logs(self, text: str) -> None:
        self.viewer.set_text(text)
        lines = [line for line in text.splitlines() if line.strip()]
        latest = lines[-1] if lines else "--"
        lowered = latest.lower()
        if "error" in lowered or "traceback" in lowered:
            signal = "Error"
        elif "warning" in lowered:
            signal = "Warning"
        elif "round" in lowered or "epoch" in lowered:
            signal = "Training"
        else:
            signal = "Info" if lines else "--"
        self.lines_card.set_value(str(len(lines)))
        self.lines_card.set_caption("Merged stdout collected by the desktop runtime manager")
        self.last_level_card.set_value(signal)
        self.last_level_card.set_caption("Derived from the newest visible log line")
        self.last_line_card.set_value(latest[:48] + ("..." if len(latest) > 48 else ""))
        self.last_line_card.set_caption("Latest console entry preview")
        self.notes.setPlainText(
            "\n".join(
                [
                    "Look for the newest error or traceback first if the run failed.",
                    "Round-completion lines confirm that metrics and artifacts should continue updating.",
                    "If logs stop but status remains Running, the child process may be stalled or waiting on IO.",
                ]
            )
        )
