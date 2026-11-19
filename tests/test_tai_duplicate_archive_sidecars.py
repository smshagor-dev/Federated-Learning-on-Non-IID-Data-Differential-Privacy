"""Regression tests: duplicate archive identity includes sidecar bytes."""
import copy
from pathlib import Path
import pytest
from scripts import audit_tai_primary_collection as audit

def sample():
    return {
        "run_id": "same-run",
        "request": {
            "dataset": "CIFAR10", "partition": "dirichlet:0.1", "seed": 23,
            "method": "fedavg", "rounds": 50,
            "train_partition_hash": "train", "test_partition_hash": "test",
            "source_commit_sha": "source",
            "training": {"adaptation_steps": 1},
        },
        "metrics": {
            "micro_accuracy": .25, "p10_client_accuracy": .12,
            "worst_client_accuracy": .05, "jain_accuracy_index": .91,
            "client_count": 20,
        },
    }

def setup(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    a, b = root / "a" / "result.json", root / "b" / "result.json"
    for p in (a, b):
        p.parent.mkdir(parents=True)
        p.write_text("{}", encoding="utf-8")
    records = {a: sample(), b: copy.deepcopy(sample())}
    monkeypatch.setattr(audit, "_check_cell", lambda p: records[p])
    monkeypatch.setattr(audit, "assert_primary_50round_protocol", lambda r: None)
    monkeypatch.setattr(audit, "_official_split_coverage", lambda *args: None)
    return root, a, b, records

def test_exact_duplicate_is_idempotent(tmp_path, monkeypatch):
    root, _, _, _ = setup(tmp_path, monkeypatch)
    rows, report = audit.collect([root])
    assert len(rows) == 1
    assert report["duplicate_identical_archives_ignored"] == 1

def test_modified_metrics_rejected(tmp_path, monkeypatch):
    root, _, b, records = setup(tmp_path, monkeypatch)
    records[b]["metrics"]["p10_client_accuracy"] = .99
    with pytest.raises(ValueError, match="conflicting duplicate"):
        audit.collect([root])

@pytest.mark.parametrize("filename", ["global_model.pt", "rounds.csv", "train_indices.npz"])
def test_modified_sidecar_rejected(tmp_path, monkeypatch, filename):
    root, a, b, _ = setup(tmp_path, monkeypatch)
    (a.parent / filename).write_bytes(b"original")
    (b.parent / filename).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="conflicting duplicate"):
        audit.collect([root])

def test_different_run_id_rejected(tmp_path, monkeypatch):
    root, _, b, records = setup(tmp_path, monkeypatch)
    records[b]["run_id"] = "different-run"
    with pytest.raises(ValueError, match="conflicting duplicate"):
        audit.collect([root])
