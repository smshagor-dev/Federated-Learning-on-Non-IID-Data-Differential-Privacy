"""Persistent, restart-safe per-stream sequence counters for the worker
side of signed envelopes -- Signed Client Results and Worker Lifecycle
Enforcement slice, Work Package C. See docs/message-sequences.md.

Deliberately much simpler than the coordinator's
``fl::coordinator::ReplayProtectionStore``: a worker only ever needs to
know "what is the next sequence number *I* should use for stream X,
under signing key Y" -- it never needs to validate anyone else's
sequence state, so this is a plain monotonic counter per
(signing_key_id, message_stream), not a bounded multi-worker replay
store. The documented starting value (1, matching the coordinator's
own contract -- see docs/message-sequences.md) is enforced here too:
the first call for a fresh (signing_key_id, message_stream) pair
returns 1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock


class SequenceStateError(RuntimeError):
    """Raised if the persisted sequence-state file exists but is
    corrupt/unreadable -- never silently reset to zero, since that
    would let a worker reuse a sequence number the coordinator has
    already accepted and would therefore now reject as a replay."""


@dataclass(slots=True)
class SequenceStateStore:
    path: Path
    _lock: Lock

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = Lock()
        if self.path.exists():
            try:
                json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise SequenceStateError(
                    f"failed to load sequence state from {self.path}: {error}"
                ) from error

    def _load(self) -> dict[str, int]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SequenceStateError(
                f"failed to load sequence state from {self.path}: {error}"
            ) from error
        if not isinstance(raw, dict):
            raise SequenceStateError(
                f"sequence state file {self.path} is not a JSON object"
            )
        return {str(key): int(value) for key, value in raw.items()}

    def _save(self, state: dict[str, int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        # Atomic on POSIX and on Windows for a same-volume rename onto an
        # existing file (os.replace, which Path.replace wraps, uses
        # MoveFileExW with MOVEFILE_REPLACE_EXISTING on Windows) -- same
        # atomicity guarantee this project's C++ persistence classes
        # rely on (temp-file-then-rename).
        temp_path.replace(self.path)

    def next_sequence(self, signing_key_id: str, message_stream: str) -> int:
        """Returns the next sequence number to use for this
        (signing_key_id, message_stream) pair, and durably persists it
        as used before returning -- a crash immediately after this call
        returns will never cause a sequence number to be reused, only
        (harmlessly) skipped."""
        key = f"{signing_key_id}:{message_stream}"
        with self._lock:
            state = self._load()
            next_value = state.get(key, 0) + 1
            state[key] = next_value
            self._save(state)
            return next_value

    def peek(self, signing_key_id: str, message_stream: str) -> int:
        """The last sequence number actually used (0 if none yet) --
        for diagnostics/tests, never for deciding the next value to
        hand out (that must always go through next_sequence, which is
        the only method that persists)."""
        key = f"{signing_key_id}:{message_stream}"
        with self._lock:
            return self._load().get(key, 0)
