from __future__ import annotations

import os

from PySide6.QtWidgets import QApplication

from desktop.controllers.runtime_controller import RuntimeController
from desktop.icons import load_app_icon
from desktop.main_window import MainWindow
from desktop.models.configuration import AppPaths
from desktop.services.preferences_service import PreferencesService
from desktop.theme import apply_theme


def build_paths(project_root: str, config_path: str) -> AppPaths:
    results_dir = os.path.join(project_root, "results")
    database_path = os.path.join(project_root, "artifacts", "desktop_history.sqlite3")
    preferences_path = os.path.join(project_root, "artifacts", "desktop_preferences.json")
    os.makedirs(os.path.dirname(database_path), exist_ok=True)
    return AppPaths(
        project_root=project_root,
        main_script=os.path.join(project_root, "main.py"),
        config_path=os.path.abspath(config_path),
        results_dir=results_dir,
        database_path=database_path,
        preferences_path=preferences_path,
    )


def launch_desktop_app(project_root: str, config_path: str) -> int:
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(load_app_icon())
    paths = build_paths(project_root, config_path)
    preferences = PreferencesService(paths.preferences_path).load()
    if preferences.last_config_path and os.path.exists(preferences.last_config_path):
        paths.config_path = preferences.last_config_path
    theme = apply_theme(app, preferences.theme)
    controller = RuntimeController(paths)
    if preferences.last_results_dir:
        controller.results_service.set_results_dir(preferences.last_results_dir)
    window = MainWindow(paths, controller, preferences)
    window.show()
    window.gpu_label.setText("GPU: available" if _cuda_available() else "GPU: CPU mode")
    window.statusBar().showMessage(
        f"Theme loaded | Results root: {paths.results_dir} | Accent: {theme['PRIMARY_ACCENT']}"
    )
    return app.exec()


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False
