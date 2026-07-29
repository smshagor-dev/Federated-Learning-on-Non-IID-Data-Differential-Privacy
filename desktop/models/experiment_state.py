from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ExperimentState:
    status: str = "Idle"
    process_id: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    runtime_config_path: str | None = None
    results_dir: str = "results"
    command_preview: str = ""
    summary_path: str | None = None
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
