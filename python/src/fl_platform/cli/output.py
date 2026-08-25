from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TextIO


@dataclass(slots=True)
class Console:
    verbose: bool = False

    def info(self, component: str, message: str) -> None:
        self._emit("INFO", component, message)

    def success(self, component: str, message: str) -> None:
        self._emit("SUCCESS", component, message)

    def warning(self, component: str, message: str) -> None:
        self._emit("WARNING", component, message)

    def error(self, component: str, message: str) -> None:
        self._emit("ERROR", component, message, stream=sys.stderr)

    def debug(self, component: str, message: str) -> None:
        if self.verbose:
            self._emit("DEBUG", component, message)

    def plain(self, message: str) -> None:
        self._safe_write(message, sys.stdout)

    def _emit(
        self,
        level: str,
        component: str,
        message: str,
        stream: TextIO = sys.stdout,
    ) -> None:
        timestamp = datetime.now(UTC).astimezone().strftime("%H:%M:%S")
        self._safe_write(
            f"[{timestamp}] {level:<7} {component:<14} {message}",
            stream,
        )

    def _safe_write(self, message: str, stream: TextIO) -> None:
        try:
            print(message, file=stream)
        except UnicodeEncodeError:
            encoded = message.encode(stream.encoding or "utf-8", errors="replace")
            if hasattr(stream, "buffer"):
                stream.buffer.write(encoded + b"\n")
                stream.flush()
                return
            stream.write(encoded.decode(stream.encoding or "utf-8", errors="replace"))
            stream.write("\n")
            stream.flush()
