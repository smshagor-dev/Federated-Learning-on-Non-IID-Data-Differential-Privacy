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


def _expand_experiment_choices(window: MainWindow) -> None:
    """Keep the desktop form aligned with the executable root runtime."""
    dataset_combo = window.experiment_page.dataset_combo
    known_datasets = {
        dataset_combo.itemText(index) for index in range(dataset_combo.count())
    }
    for dataset in ("MNIST", "FASHIONMNIST", "CIFAR10", "CIFAR100"):
        if dataset not in known_datasets:
            dataset_combo.addItem(dataset)

    partition_combo = window.experiment_page.partition_combo
    known_partitions = {
        partition_combo.itemText(index) for index in range(partition_combo.count())
    }
    for partition in ("iid", "dirichlet", "pathological", "quantity_skew"):
        if partition not in known_partitions:
            partition_combo.addItem(partition)

    # The page is constructed before these extended choices are added. Reload
    # once so a saved config using an expanded dataset/partition is selected.
    window.experiment_page.load_config(window.current_config, window.paths.config_path)


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
    _expand_experiment_choices(window)
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
