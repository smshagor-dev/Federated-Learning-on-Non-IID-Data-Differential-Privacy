from __future__ import annotations

import sqlite3
from typing import Any

from desktop.database.schema import RUNS_SCHEMA


class DatabaseService:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(RUNS_SCHEMA)

    def create_run(self, payload: dict[str, Any]) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO experiment_runs (
                    started_at, finished_at, status, algorithm, dataset,
                    results_dir, runtime_config_path, summary_path, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["started_at"],
                    payload.get("finished_at"),
                    payload["status"],
                    payload["algorithm"],
                    payload["dataset"],
                    payload["results_dir"],
                    payload["runtime_config_path"],
                    payload.get("summary_path"),
                    payload.get("notes"),
                ),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, finished_at: str, summary_path: str | None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE experiment_runs
                SET status = ?, finished_at = ?, summary_path = ?
                WHERE id = ?
                """,
                (status, finished_at, summary_path, run_id),
            )

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, started_at, finished_at, status, algorithm, dataset,
                       results_dir, runtime_config_path, summary_path, notes
                FROM experiment_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
