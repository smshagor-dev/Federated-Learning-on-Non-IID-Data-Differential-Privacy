from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QStyle, QWidget


_NAV_ICON_NAMES = {
    "dashboard": QStyle.SP_ComputerIcon,
    "experiment": QStyle.SP_FileIcon,
    "live_training": QStyle.SP_MediaPlay,
    "clients": QStyle.SP_DirIcon,
    "algorithms": QStyle.SP_BrowserReload,
    "privacy": QStyle.SP_MessageBoxWarning,
    "metrics": QStyle.SP_FileDialogContentsView,
    "results": QStyle.SP_DialogApplyButton,
    "artifacts": QStyle.SP_DriveHDIcon,
    "logs": QStyle.SP_FileDialogDetailedView,
    "settings": QStyle.SP_FileDialogInfoView,
}

_ACTION_ICON_NAMES = {
    "new_experiment": QStyle.SP_FileDialogNewFolder,
    "open_project": QStyle.SP_DirOpenIcon,
    "import_config": QStyle.SP_ArrowDown,
    "menu": QStyle.SP_TitleBarMenuButton,
    "browse": QStyle.SP_DirOpenIcon,
    "reload": QStyle.SP_BrowserReload,
    "run": QStyle.SP_MediaPlay,
    "stop": QStyle.SP_MediaStop,
    "apply": QStyle.SP_DialogApplyButton,
}


def load_app_icon() -> QIcon:
    icon_path = Path(__file__).resolve().parent / "resources" / "icons" / "app_icon.svg"
    return QIcon(str(icon_path))


def nav_icon(widget: QWidget, key: str) -> QIcon:
    return widget.style().standardIcon(_NAV_ICON_NAMES.get(key, QStyle.SP_FileIcon))


def action_icon(widget: QWidget, key: str) -> QIcon:
    return widget.style().standardIcon(_ACTION_ICON_NAMES.get(key, QStyle.SP_FileIcon))
