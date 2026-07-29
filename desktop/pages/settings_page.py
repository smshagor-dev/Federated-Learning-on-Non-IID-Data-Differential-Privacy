from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop.models.preferences import DesktopPreferences
from desktop.widgets.metric_card import MetricCard
from desktop.widgets.research_card import ResearchCard
from desktop.widgets.section_header import SectionHeader


class SettingsPage(QWidget):
    preferences_applied = Signal(dict)
    open_results_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(
            SectionHeader(
                "Settings",
                "Desktop-level theme, refresh, startup, and workspace customization for the research studio.",
            )
        )

        cards = QGridLayout()
        cards.setSpacing(10)
        self.workspace_card = MetricCard("Workspace", "--", "Active project root")
        self.database_card = MetricCard("History Database", "--", "Local desktop run registry")
        self.results_card = MetricCard("Results Root", "--", "Current active output directory")
        self.theme_card = MetricCard("Theme", "light", "Applied Qt stylesheet mode")
        cards.addWidget(self.workspace_card, 0, 0)
        cards.addWidget(self.database_card, 0, 1)
        cards.addWidget(self.results_card, 0, 2)
        cards.addWidget(self.theme_card, 0, 3)
        layout.addLayout(cards)

        body = QGridLayout()
        body.setSpacing(10)

        settings_card = ResearchCard()
        form = QFormLayout()
        self.project_root = QLineEdit()
        self.project_root.setReadOnly(True)
        self.database_path = QLineEdit()
        self.database_path.setReadOnly(True)
        self.results_dir = QLineEdit()
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["light", "dark"])
        self.refresh_combo = QComboBox()
        self.refresh_combo.addItems(["1000", "2000", "3000", "5000"])
        self.start_page_combo = QComboBox()
        self.start_page_combo.addItems(
            [
                "Dashboard",
                "New Experiment",
                "Live Training",
                "Client Distribution",
                "Algorithms",
                "Differential Privacy",
                "Metrics and Charts",
                "Experiment Results",
                "Artifacts",
                "Runtime Logs",
                "Settings",
            ]
        )
        self.auto_refresh_checkbox = QCheckBox("Enable auto-refresh snapshot polling")
        form.addRow("Project Root", self.project_root)
        form.addRow("History Database", self.database_path)
        form.addRow("Results Directory", self.results_dir)
        form.addRow("Theme", self.theme_combo)
        form.addRow("Refresh Interval (ms)", self.refresh_combo)
        form.addRow("Startup Page", self.start_page_combo)
        form.addRow("Live Updates", self.auto_refresh_checkbox)
        settings_card.layout.addWidget(SectionHeader("Application Settings", "Editable preferences that affect how the desktop shell behaves."))
        settings_card.layout.addLayout(form)
        actions = QHBoxLayout()
        self.open_results_button = QPushButton("Open Results Folder")
        self.apply_button = QPushButton("Apply Preferences")
        self.apply_button.setProperty("primary", True)
        actions.addWidget(self.open_results_button)
        actions.addStretch(1)
        actions.addWidget(self.apply_button)
        settings_card.layout.addLayout(actions)
        body.addWidget(settings_card, 0, 0)

        self.notes_card = ResearchCard()
        self.notes_card.layout.addWidget(
            SectionHeader(
                "Workspace Notes",
                "Operational context for the desktop shell and where state is persisted.",
            )
        )
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes_card.layout.addWidget(self.notes, 1)
        body.addWidget(self.notes_card, 0, 1)

        layout.addLayout(body, 1)

        self.apply_button.clicked.connect(self._emit_preferences)
        self.open_results_button.clicked.connect(lambda: self.open_results_requested.emit(self.results_dir.text().strip()))

    def set_paths(self, project_root: str, database_path: str, results_dir: str) -> None:
        self.project_root.setText(project_root)
        self.database_path.setText(database_path)
        self.results_dir.setText(results_dir)
        self.workspace_card.set_value(self._leaf_name(project_root))
        self.workspace_card.set_caption(project_root)
        self.database_card.set_value(self._leaf_name(database_path))
        self.database_card.set_caption(database_path)
        self.results_card.set_value(self._leaf_name(results_dir))
        self.results_card.set_caption(results_dir)
        self._refresh_notes()

    def load_preferences(self, preferences: DesktopPreferences) -> None:
        self.theme_combo.setCurrentText(preferences.theme)
        self.refresh_combo.setCurrentText(str(preferences.refresh_interval_ms))
        self.start_page_combo.setCurrentIndex(preferences.start_page)
        self.auto_refresh_checkbox.setChecked(preferences.auto_refresh)
        self.theme_card.set_value(preferences.theme)
        self.theme_card.set_caption("Applied Qt stylesheet mode")
        self._refresh_notes()

    def _emit_preferences(self) -> None:
        payload = {
            "theme": self.theme_combo.currentText(),
            "refresh_interval_ms": int(self.refresh_combo.currentText()),
            "start_page": self.start_page_combo.currentIndex(),
            "auto_refresh": self.auto_refresh_checkbox.isChecked(),
            "results_dir": self.results_dir.text().strip(),
        }
        self.theme_card.set_value(payload["theme"])
        self.theme_card.set_caption("Applied Qt stylesheet mode")
        self._refresh_notes()
        self.preferences_applied.emit(payload)

    def _refresh_notes(self) -> None:
        self.notes.setPlainText(
            "\n".join(
                [
                    "Project root controls where the desktop runtime resolves main.py and local artifacts.",
                    "History database stores desktop-triggered run metadata and recent experiment state.",
                    f"Results directory currently targets: {self.results_dir.text().strip() or '--'}",
                    f"Theme: {self.theme_combo.currentText()} | Refresh: {self.refresh_combo.currentText()} ms | Auto-refresh: {self.auto_refresh_checkbox.isChecked()}",
                ]
            )
        )

    @staticmethod
    def _leaf_name(path: str) -> str:
        parts = path.replace("\\", "/").rstrip("/").split("/")
        return parts[-1] if parts else path
