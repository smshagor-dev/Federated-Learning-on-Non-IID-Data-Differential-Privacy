from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AuditEvent:
    event_type: str
    actor_id: str
    timestamp: str
    run_id: str = ""
    round_id: int | None = None
    outcome: str = "accepted"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AuditLog:
    events: list[AuditEvent] = field(default_factory=list)

    def append(self, event: AuditEvent) -> None:
        if not event.event_type:
            raise ValueError("event_type must not be empty")
        if not event.actor_id:
            raise ValueError("actor_id must not be empty")
        if not event.timestamp:
            raise ValueError("timestamp must not be empty")
        self.events.append(event)

    def filter_by_run(self, run_id: str) -> list[AuditEvent]:
        return [event for event in self.events if event.run_id == run_id]

    def filter_by_type(self, event_type: str) -> list[AuditEvent]:
        return [event for event in self.events if event.event_type == event_type]
