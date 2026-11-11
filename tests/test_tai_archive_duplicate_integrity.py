"""Regression: duplicated run IDs must not conceal changed experimental evidence."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts import audit_tai_primary_collection as audit


def _result() -> dict:
    return {
        "run_id": "same-run-id",
        "request": {
            "dataset": "CIFAR10", "partition": "dirichlet:0.1",
            "seed": 23, "method": "fedavg", "rounds": 50,
            "train_partition_hash": "train-hash",
            "test_partition_hash": "test-hash",
            "source_commit_sha": "frozen-source",
            "training": {"adaptation_steps": 1},
        },
        "metrics": {
            "micro_accuracy": 0.25, "p10_client_accuracy": 0.12,
            "worst_client_accuracy": 0.05, "jain_accuracy_index": 0.91,
            "client_count": 20,
        },
    }


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: bool):
    root = tmp_path / "archives"
    first = root / "a" / "result.json"
    second = root / "b" / "result.json"
    for file in (first, second):
        file.parent.mkdir(parents=True)
        file.write_text("{}")
    original = _result()
    changed = copy.deepcopy(original)
    if mutate:
        changed["metrics"]["p10_client_accuracy"] = 0.99
    mapping = {first: original, second: changed}
    monkeypatch.setattr(audit, "_check_cell", lambda path: mapping[path])
    monkeypatch.setattr(audit, "assert_primary_50round_protocol", lambda result: None)
    monkeypatch.setattr(audit, "_official_split_coverage", lambda *args: None)
    return audit.collect([root])


def test_same_run_id_with_modified_metrics_is_rejected(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="conflicting duplicate experiment evidence"):
        _run(tmp_path, monkeypatch, mutate=True)


def test_exact_repeated_archive_is_counted_once(tmp_path, monkeypatch):
    rows, report = _run(tmp_path, monkeypatch, mutate=False)
    assert len(rows) == 1
    assert report["primary"]["verified"] == 1
    assert report["duplicate_identical_archives_ignored"] == 1
