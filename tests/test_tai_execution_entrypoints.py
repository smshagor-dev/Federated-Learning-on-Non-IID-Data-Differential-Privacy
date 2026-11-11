"""Regression tests for direct-script imports after long-running experiments.

A full 50-round real-data run previously finished successfully, but its
orchestrator failed while importing tai_pfl.analysis from scripts/ rather
than repository root. These tests must fail if that regression reappears.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run_direct(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-I", script, *args],
        cwd=REPO,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_primary_launcher_direct_script_is_importable_without_pythonpath() -> None:
    outcome = _run_direct(
        "scripts/run_tai_1680.py",
        "--shard-count",
        "1680",
        "--shard-index",
        "0",
    )
    data = json.loads(outcome.stdout)
    assert data["status"] == "PLANNED_NOT_EXECUTED"
    assert data["expected_full_matrix"] == 1680
    assert data["assigned_shard_cells"] == 1
    assert data["exact_expected_cells"] == [
        {
            "dataset": "MNIST",
            "condition": "iid",
            "method": "fedavg",
            "seed": 11,
            "rounds": 50,
        }
    ]


def test_rq4_direct_script_loads_repo_modules_without_pythonpath() -> None:
    outcome = _run_direct("scripts/run_tai_rq4_from_primary.py", "--help")
    assert "RQ4" in outcome.stdout
    assert "--primary-cell" in outcome.stdout
    assert "--output-root" in outcome.stdout


def test_subprocess_import_environment_contains_repository_path() -> None:
    script = (REPO / "scripts/run_tai_1680.py").read_text(encoding="utf-8")
    assert "PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]" in script
    assert "env['PYTHONPATH'] = str(PROJECT_ROOT)" in script
