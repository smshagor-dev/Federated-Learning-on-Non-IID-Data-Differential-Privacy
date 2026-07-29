from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


LIGHT_THEME = {
    "BACKGROUND": "#f4f7fb",
    "SURFACE": "#ffffff",
    "SURFACE_ALTERNATIVE": "#eef3f8",
    "PRIMARY_TEXT": "#16202b",
    "SECONDARY_TEXT": "#607284",
    "PRIMARY_ACCENT": "#0f766e",
    "SECONDARY_ACCENT": "#d97706",
    "BORDER": "#d7e0ea",
    "SUCCESS": "#0b8f55",
    "WARNING": "#d97706",
    "ERROR": "#c2410c",
    "SHADOW": "#e9eef5",
    "LOG_SURFACE": "#101820",
    "LOG_TEXT": "#e6eef7",
}


DARK_THEME = {
    "BACKGROUND": "#18222d",
    "SURFACE": "#223142",
    "SURFACE_ALTERNATIVE": "#26384c",
    "PRIMARY_TEXT": "#ecf2f8",
    "SECONDARY_TEXT": "#b1c0cf",
    "PRIMARY_ACCENT": "#199081",
    "SECONDARY_ACCENT": "#dc8a1a",
    "BORDER": "#34495d",
    "SUCCESS": "#3bc27a",
    "WARNING": "#e7a032",
    "ERROR": "#ea7753",
    "SHADOW": "#101820",
    "LOG_SURFACE": "#101820",
    "LOG_TEXT": "#e6eef7",
}


def apply_theme(app: QApplication, mode: str = "light") -> dict[str, str]:
    theme = DARK_THEME if mode == "dark" else LIGHT_THEME
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(theme["BACKGROUND"]))
    palette.setColor(QPalette.WindowText, QColor(theme["PRIMARY_TEXT"]))
    palette.setColor(QPalette.Base, QColor(theme["SURFACE"]))
    palette.setColor(QPalette.AlternateBase, QColor(theme["SURFACE_ALTERNATIVE"]))
    palette.setColor(QPalette.Text, QColor(theme["PRIMARY_TEXT"]))
    palette.setColor(QPalette.Button, QColor(theme["SURFACE"]))
    palette.setColor(QPalette.ButtonText, QColor(theme["PRIMARY_TEXT"]))
    palette.setColor(QPalette.Highlight, QColor(theme["PRIMARY_ACCENT"]))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    app.setStyleSheet(_render_qss(mode, theme))
    return theme


def _render_qss(mode: str, theme: dict[str, str]) -> str:
    qss_path = Path(__file__).resolve().parent / "resources" / "styles" / f"{mode}.qss"
    template = qss_path.read_text(encoding="utf-8")
    return template.format(**theme)
