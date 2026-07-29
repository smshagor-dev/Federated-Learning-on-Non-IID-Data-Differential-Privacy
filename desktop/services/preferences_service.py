from __future__ import annotations

import json
import os

from desktop.models.preferences import DesktopPreferences


class PreferencesService:
    def __init__(self, preferences_path: str) -> None:
        self.preferences_path = preferences_path

    def load(self) -> DesktopPreferences:
        if not os.path.exists(self.preferences_path):
            return DesktopPreferences()
        try:
            with open(self.preferences_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return DesktopPreferences()
        return DesktopPreferences.from_dict(payload)

    def save(self, preferences: DesktopPreferences) -> None:
        os.makedirs(os.path.dirname(self.preferences_path), exist_ok=True)
        with open(self.preferences_path, "w", encoding="utf-8") as handle:
            json.dump(preferences.to_dict(), handle, indent=2)
