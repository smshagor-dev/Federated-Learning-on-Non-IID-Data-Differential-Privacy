from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NavItem:
    key: str
    label: str


NAV_ITEMS = [
    NavItem("dashboard", "Dashboard"),
    NavItem("experiment", "New Experiment"),
    NavItem("live_training", "Live Training"),
    NavItem("clients", "Client Distribution"),
    NavItem("algorithms", "Algorithms"),
    NavItem("privacy", "Differential Privacy"),
    NavItem("metrics", "Metrics and Charts"),
    NavItem("results", "Experiment Results"),
    NavItem("artifacts", "Artifacts"),
    NavItem("logs", "Runtime Logs"),
    NavItem("settings", "Settings"),
]
