from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class NonceReplayGuard:
    """In-memory replay guard scaffold.

    Milestone scope:
    - deterministic registration
    - per-scope nonce uniqueness
    - no distributed persistence yet
    """

    seen: dict[str, set[str]] = field(default_factory=dict)

    def register(self, scope: str, nonce: str) -> bool:
        if not scope:
            raise ValueError("scope must not be empty")
        if not nonce:
            raise ValueError("nonce must not be empty")
        bucket = self.seen.setdefault(scope, set())
        if nonce in bucket:
            return False
        bucket.add(nonce)
        return True

    def has_seen(self, scope: str, nonce: str) -> bool:
        return nonce in self.seen.get(scope, set())
