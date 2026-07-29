from __future__ import annotations

from functools import partial
import os

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from desktop.controllers.runtime_controller import RuntimeController
from desktop.icons import action_icon, load_app_icon, nav_icon
from desktop.models.configuration import AppPaths
from desktop.models.preferences import DesktopPreferences
from desktop.navigation import NAV_ITEMS
from desktop.pages.algorithms_page import AlgorithmsPage
from desktop.pages.artifacts_page import ArtifactsPage
from desktop.pages.clients_page import ClientsPage
from desktop.pages.dashboard_page import DashboardPage
from desktop.pages.experiment_page import ExperimentPage
from desktop.pages.live_training_page import LiveTrainingPage
from desktop.pages.logs_page import LogsPage
from desktop.pages.metrics_page import MetricsPage
from desktop.pages.privacy_page import PrivacyPage
from desktop.pages.results_page import ResultsPage
from desktop.pages.settings_page import SettingsPage
from desktop.services.preferences_service import PreferencesService
from desktop.theme import apply_theme


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths, controller: RuntimeController, preferences: DesktopPreferences) -> None:
        super().__init__()
        self.paths = paths
        self.controller = controller
        self.preferences_service = PreferencesService(paths.preferences_path)
        self.preferences = preferences
        self.theme_name = preferences.theme
        self.current_config = controller.load_config(paths.config_path)
        self.sidebar_expanded = True
        self.nav_buttons: list[QPushButton] = []

        self.setWindowTitle("Federated DP Research Studio")
        self.setWindowIcon(load_app_icon())
        self.resize(1500, 980)
        self.setMinimumSize(1120, 720)

        root = QWidget()
        root.setObjectName("AppSurface")
        shell = QVBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._build_header())

        center_shell = QWidget()
        center_layout = QHBoxLayout(center_shell)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        self.sidebar = self._build_sidebar()
        center_layout.addWidget(self.sidebar, 0)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(14, 12, 14, 10)
        content_layout.setSpacing(0)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)
        center_layout.addWidget(content, 1)

        shell.addWidget(center_shell, 1)
        shell.addWidget(self._build_footer())
        self.setCentralWidget(root)

        self.dashboard_page = DashboardPage()
        self.experiment_page = ExperimentPage(list(controller.supported_algorithms()))
        self.live_training_page = LiveTrainingPage()
        self.clients_page = ClientsPage()
        self.algorithms_page = AlgorithmsPage()
        self.privacy_page = PrivacyPage()
        self.metrics_page = MetricsPage()
        self.results_page = ResultsPage()
        self.artifacts_page = ArtifactsPage()
        self.logs_page = LogsPage()
        self.settings_page = SettingsPage()

        for page in [
            self.dashboard_page,
            self.experiment_page,
            self.live_training_page,
            self.clients_page,
            self.algorithms_page,
            self.privacy_page,
            self.metrics_page,
            self.results_page,
            self.artifacts_page,
            self.logs_page,
            self.settings_page,
        ]:
            self.stack.addWidget(page)

        self.experiment_page.load_config(self.current_config, self.paths.config_path)
        self.settings_page.set_paths(paths.project_root, paths.database_path, controller.results_service.results_dir)
        self.settings_page.load_preferences(preferences)
        self.algorithms_page.update_view(controller.supported_algorithms(), self.current_config)

        self.experiment_page.run_requested.connect(self._run_experiment)
        self.experiment_page.stop_requested.connect(controller.stop_run)
        self.experiment_page.browse_config_requested.connect(self._browse_config)
        self.experiment_page.reload_requested.connect(self._reload_config)
        self.artifacts_page.open_artifact_requested.connect(self._open_artifact)
        self.settings_page.preferences_applied.connect(self._apply_preferences)
        self.settings_page.open_results_requested.connect(self._open_results_folder)
        self.quick_new_button.clicked.connect(partial(self._select_page, 1))
        self.quick_open_button.clicked.connect(self._open_project_root)
        self.quick_import_button.clicked.connect(self._browse_config)
        self.settings_button.clicked.connect(partial(self._select_page, 10))
        self.dashboard_page.view_logs_requested.connect(partial(self._select_page, 9))
        self.dashboard_page.view_clients_requested.connect(partial(self._select_page, 3))
        self.dashboard_page.view_artifacts_requested.connect(partial(self._select_page, 8))
        self.dashboard_page.new_experiment_requested.connect(partial(self._select_page, 1))

        controller.snapshot_changed.connect(self._apply_snapshot)
        controller.log_changed.connect(self._apply_logs)
        controller.status_changed.connect(self._apply_status)

        self._select_page(min(preferences.start_page, self.stack.count() - 1))

        status_bar = QStatusBar()
        status_bar.showMessage(f"Theme: {self.theme_name} | Results: {controller.results_service.results_dir}")
        self.setStatusBar(status_bar)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(preferences.refresh_interval_ms)
        self.refresh_timer.timeout.connect(controller.publish_snapshot)
        if preferences.auto_refresh:
            self.refresh_timer.start()
        self._set_sidebar_state(preferences.sidebar_expanded)
        self._apply_config_labels()
        QTimer.singleShot(0, controller.publish_snapshot)

    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setProperty("headerCard", True)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(16)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        title = QLabel("Federated DP Research Studio")
        title.setProperty("heroTitle", True)
        subtitle = QLabel(
            "Professional desktop environment for non-IID federated learning,\nprivacy analysis and reproducible experiments."
        )
        subtitle.setProperty("muted", True)
        left.addWidget(title)
        left.addWidget(subtitle)
        layout.addLayout(left, 1)

        right = QHBoxLayout()
        right.setSpacing(10)
        self.run_badge = self._build_info_badge("RUNNING", "Experiment Active", "status")
        self.device_badge = self._build_info_badge("CUDA", "GPU Available", "device")
        self.database_badge = self._build_info_badge("PostgreSQL", "Primary Database", "database")
        self.experiment_badge = self._build_info_badge("Experiment", "exp-preview", "experiment")
        self.gpu_label = self.device_badge.subtitle_label
        for widget in [self.run_badge, self.device_badge, self.database_badge, self.experiment_badge]:
            right.addWidget(widget)
        self.settings_button = QPushButton()
        self.settings_button.setProperty("iconButton", True)
        self.settings_button.setIcon(action_icon(self, "menu"))
        self.settings_button.setFixedSize(34, 34)
        right.addWidget(self.settings_button)
        layout.addLayout(right)
        return frame

    def _build_info_badge(self, title: str, subtitle: str, kind: str) -> QFrame:
        card = QFrame()
        card.setProperty("badgeCard", True)
        card.setProperty("badgeKind", kind)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        top = QLabel(title)
        top.setProperty("badgeTitle", True)
        bottom = QLabel(subtitle)
        bottom.setProperty("badgeSubtitle", True)
        layout.addWidget(top)
        layout.addWidget(bottom)
        card.title_label = top  # type: ignore[attr-defined]
        card.subtitle_label = bottom  # type: ignore[attr-defined]
        return card

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setProperty("sidebar", True)
        sidebar.setFixedWidth(220)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        for index, item in enumerate(NAV_ITEMS):
            button = QPushButton(item.label)
            button.setProperty("navButton", True)
            button.setToolTip(item.label)
            button.setMinimumHeight(38)
            button.setIcon(nav_icon(self, item.key))
            button.setIconSize(button.iconSize())
            button.clicked.connect(partial(self._select_page, index))
            self.nav_buttons.append(button)
            layout.addWidget(button)

        layout.addStretch(1)

        quick_card = QFrame()
        quick_card.setProperty("card", True)
        quick_layout = QVBoxLayout(quick_card)
        quick_layout.setContentsMargins(10, 10, 10, 10)
        quick_layout.setSpacing(8)
        quick_title = QLabel("Quick Actions")
        quick_title.setProperty("sectionTitle", True)
        quick_layout.addWidget(quick_title)
        self.quick_new_button = QPushButton("New Experiment")
        self.quick_new_button.setProperty("primary", True)
        self.quick_new_button.setIcon(action_icon(self, "new_experiment"))
        self.quick_open_button = QPushButton("Open Project")
        self.quick_open_button.setIcon(action_icon(self, "open_project"))
        self.quick_import_button = QPushButton("Import Configuration")
        self.quick_import_button.setIcon(action_icon(self, "import_config"))
        quick_layout.addWidget(self.quick_new_button)
        quick_layout.addWidget(self.quick_open_button)
        quick_layout.addWidget(self.quick_import_button)
        layout.addWidget(quick_card)

        self.toggle_button = QPushButton("Collapse")
        self.toggle_button.setProperty("subtleButton", True)
        self.toggle_button.clicked.connect(self._toggle_sidebar)
        layout.addWidget(self.toggle_button)
        return sidebar

    def _build_footer(self) -> QWidget:
        frame = QFrame()
        frame.setProperty("footerCard", True)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(18)

        progress_label = QLabel("Training Progress")
        progress_label.setProperty("footerLabel", True)
        layout.addWidget(progress_label)

        self.footer_progress_track = QFrame()
        self.footer_progress_track.setProperty("progressTrack", True)
        track_layout = QHBoxLayout(self.footer_progress_track)
        track_layout.setContentsMargins(0, 0, 0, 0)
        self.footer_progress_fill = QFrame()
        self.footer_progress_fill.setProperty("progressFill", True)
        self.footer_progress_fill.setFixedWidth(86)
        track_layout.addWidget(self.footer_progress_fill)
        track_layout.addStretch(1)
        self.footer_progress_track.setFixedWidth(122)
        self.footer_progress_track.setFixedHeight(14)
        layout.addWidget(self.footer_progress_track)

        self.progress_percent = QLabel("0%")
        self.progress_percent.setProperty("footerValue", True)
        layout.addWidget(self.progress_percent)

        self.current_task_label = QLabel("Current Task")
        self.current_task_label.setProperty("footerLabel", True)
        self.current_task_value = QLabel("Idle")
        self.current_task_value.setProperty("footerValue", True)
        layout.addWidget(self.current_task_label)
        layout.addWidget(self.current_task_value, 1)

        self.command_label = QLabel("Active Command")
        self.command_label.setProperty("footerLabel", True)
        self.command_value = QLabel("python main.py --cli --config config.yaml")
        self.command_value.setProperty("footerValue", True)
        layout.addWidget(self.command_label)
        layout.addWidget(self.command_value, 2)

        self.db_label = QLabel("Database")
        self.db_label.setProperty("footerLabel", True)
        self.db_value = QLabel("PostgreSQL")
        self.db_value.setProperty("footerValue", True)
        layout.addWidget(self.db_label)
        layout.addWidget(self.db_value)

        self.device_label = QLabel("Device")
        self.device_label.setProperty("footerLabel", True)
        self.device_value = QLabel("CUDA")
        self.device_value.setProperty("footerValue", True)
        layout.addWidget(self.device_label)
        layout.addWidget(self.device_value)

        self.results_label = QLabel("Results Directory")
        self.results_label.setProperty("footerLabel", True)
        self.results_value = QLabel("results")
        self.results_value.setProperty("footerValue", True)
        layout.addWidget(self.results_label)
        layout.addWidget(self.results_value, 1)
        return frame

    def _toggle_sidebar(self) -> None:
        self._set_sidebar_state(not self.sidebar_expanded)

    def _set_sidebar_state(self, expanded: bool) -> None:
        self.sidebar_expanded = expanded
        if expanded:
            self.sidebar.setFixedWidth(220)
            self.toggle_button.setText("Collapse")
            self.quick_new_button.show()
            self.quick_open_button.show()
            self.quick_import_button.show()
            for button, item in zip(self.nav_buttons, NAV_ITEMS, strict=True):
                button.setText(item.label)
        else:
            self.sidebar.setFixedWidth(72)
            self.toggle_button.setText("Expand")
            self.quick_new_button.hide()
            self.quick_open_button.hide()
            self.quick_import_button.hide()
            for button, item in zip(self.nav_buttons, NAV_ITEMS, strict=True):
                button.setText("".join(word[0] for word in item.label.split())[:2].upper())
        self.preferences.sidebar_expanded = self.sidebar_expanded

    def _select_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setProperty("active", button_index == index)
            button.style().unpolish(button)
            button.style().polish(button)

    def _browse_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select YAML config",
            self.paths.project_root,
            "YAML Files (*.yaml *.yml)",
        )
        if path:
            self.paths.config_path = path
            self.preferences.last_config_path = path
            self._reload_config()

    def _reload_config(self) -> None:
        self.current_config = self.controller.load_config(self.paths.config_path)
        self.experiment_page.load_config(self.current_config, self.paths.config_path)
        self.algorithms_page.update_view(self.controller.supported_algorithms(), self.current_config)
        self._apply_config_labels()
        self.preferences.last_config_path = self.paths.config_path
        self.controller.append_manual_log(f"Reloaded config: {self.paths.config_path}")

    def _run_experiment(self, updates: dict) -> None:
        try:
            runtime_config = self.controller.build_runtime_config(self.current_config, updates)
            runtime_path = self.controller.start_run(runtime_config)
        except Exception as exc:
            QMessageBox.critical(self, "Run failed", str(exc))
            return
        self.current_config = runtime_config
        self.settings_page.set_paths(
            self.paths.project_root,
            self.paths.database_path,
            runtime_config["system"]["results_dir"],
        )
        self.experiment_page.load_config(runtime_config, runtime_path)
        self._apply_config_labels()
        self.command_value.setText(f"{self.paths.main_script} --cli --config {runtime_path}")
        self.current_task_value.setText("Experiment running")
        self.preferences.last_results_dir = runtime_config["system"]["results_dir"]
        self._select_page(2)

    def _apply_snapshot(self, snapshot: dict) -> None:
        clients_frame = snapshot["clients_frame"]
        self.dashboard_page.update_snapshot(snapshot, self.current_config, clients_frame)
        self.metrics_page.update_snapshot(snapshot["metrics"])
        self.results_page.set_summary(snapshot["summary"])
        self.artifacts_page.load_artifacts(snapshot["artifacts"])
        self.live_training_page.update_process_state(snapshot["process_state"], snapshot["metrics"])
        self.privacy_page.update_view(self.current_config, snapshot["metrics"])
        self.clients_page.load_frame(clients_frame)
        self.settings_page.set_paths(
            self.paths.project_root,
            self.paths.database_path,
            snapshot["results_dir"],
        )
        self.command_value.setText(snapshot["process_state"].command_preview or "python main.py --cli --config config.yaml")
        self.results_value.setText(snapshot["results_dir"])
        self.preferences.last_results_dir = snapshot["results_dir"]
        self._update_progress(snapshot)
        self.dashboard_page.set_active_page(self.stack.currentIndex() == 0)

    def _apply_logs(self, text: str) -> None:
        self.live_training_page.set_logs(text)
        self.logs_page.set_logs(text)
        self.dashboard_page.set_logs(text)

    def _apply_status(self, status: str) -> None:
        self.run_badge.title_label.setText(status.upper())
        self.run_badge.subtitle_label.setText("Experiment Active" if status == "Running" else "Desktop Runtime")
        self.current_task_value.setText(status)
        self._sync_badge_style(self.run_badge, status)

    def _sync_badge_style(self, badge: QFrame, status: str) -> None:
        badge.setProperty("runtimeState", status.lower())
        badge.style().unpolish(badge)
        badge.style().polish(badge)

    def _open_artifact(self, path: str) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _set_theme_label(self, theme_name: str) -> None:
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme_name)
        self.theme_name = theme_name
        self.preferences.theme = theme_name
        self.statusBar().showMessage(
            f"Theme: {theme_name} | Results: {self.controller.results_service.results_dir}"
        )

    def _apply_config_labels(self) -> None:
        algorithm = self.current_config["algorithm"]["name"]
        device = self.current_config["system"]["device"]
        results_dir = self.current_config["system"]["results_dir"]
        database_name = "PostgreSQL" if "postgres" in self.paths.database_path.lower() else "SQLite"
        self.device_badge.title_label.setText(device.upper())
        self.device_badge.subtitle_label.setText("CUDA Available" if "cuda" in device.lower() else "CPU Execution")
        self.database_badge.title_label.setText(database_name)
        self.database_badge.subtitle_label.setText("Primary Database")
        self.experiment_badge.title_label.setText("Experiment")
        self.experiment_badge.subtitle_label.setText(results_dir.split("/")[-1].split("\\")[-1] or algorithm)
        self.db_value.setText(database_name)
        self.device_value.setText(device.upper())
        self.results_value.setText(results_dir)

    def _apply_preferences(self, payload: dict) -> None:
        self.preferences.theme = payload["theme"]
        self.preferences.refresh_interval_ms = payload["refresh_interval_ms"]
        self.preferences.start_page = payload["start_page"]
        self.preferences.auto_refresh = payload["auto_refresh"]
        if payload["results_dir"]:
            results_dir = payload["results_dir"]
            if not os.path.isabs(results_dir):
                results_dir = os.path.join(self.paths.project_root, results_dir)
            self.controller.results_service.set_results_dir(results_dir)
            self.preferences.last_results_dir = results_dir
            self.current_config["system"]["results_dir"] = results_dir
            self.experiment_page.results_dir_edit.setText(results_dir)
            self.results_value.setText(results_dir)
            self.settings_page.set_paths(self.paths.project_root, self.paths.database_path, results_dir)
        self._set_theme_label(payload["theme"])
        self.refresh_timer.setInterval(payload["refresh_interval_ms"])
        if payload["auto_refresh"]:
            if not self.refresh_timer.isActive():
                self.refresh_timer.start()
        else:
            self.refresh_timer.stop()
        self.preferences_service.save(self.preferences)
        self.controller.publish_snapshot()

    def _open_results_folder(self, path: str) -> None:
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_project_root(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.paths.project_root))

    def _update_progress(self, snapshot: dict) -> None:
        rounds_total = int(self.current_config["federated"]["rounds"])
        latest_round = int(snapshot["metrics"]["latest_round"])
        progress = 0 if rounds_total <= 0 else min(100, round((latest_round / rounds_total) * 100))
        fill_width = max(8, round(122 * (progress / 100))) if progress > 0 else 8
        self.footer_progress_fill.setFixedWidth(fill_width)
        self.progress_percent.setText(f"{progress}%")

    def closeEvent(self, event: QCloseEvent) -> None:
        self.preferences.theme = self.theme_name
        self.preferences.sidebar_expanded = self.sidebar_expanded
        self.preferences.last_config_path = self.paths.config_path
        self.preferences.last_results_dir = self.controller.results_service.results_dir
        self.preferences_service.save(self.preferences)
        super().closeEvent(event)
