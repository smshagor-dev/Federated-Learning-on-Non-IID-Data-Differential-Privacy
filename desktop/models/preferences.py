from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class DesktopPreferences:
    theme: str = "light"
    refresh_interval_ms: int = 2000
    auto_refresh: bool = True
    start_page: int = 0
    sidebar_expanded: bool = True
    last_config_path: str = ""
    last_results_dir: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "DesktopPreferences":
        return cls(
            theme=str(payload.get("theme", "light")),
            refresh_interval_ms=max(500, int(payload.get("refresh_interval_ms", 2000))),
            auto_refresh=bool(payload.get("auto_refresh", True)),
            start_page=max(0, int(payload.get("start_page", 0))),
            sidebar_expanded=bool(payload.get("sidebar_expanded", True)),
            last_config_path=str(payload.get("last_config_path", "")),
            last_results_dir=str(payload.get("last_results_dir", "")),
        )
