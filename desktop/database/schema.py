from __future__ import annotations


RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiment_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    dataset TEXT NOT NULL,
    results_dir TEXT NOT NULL,
    runtime_config_path TEXT NOT NULL,
    summary_path TEXT,
    notes TEXT
);
"""
