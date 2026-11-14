"""Synthetic file fixtures test recovery guards; never count as real research."""
from __future__ import annotations

import hashlib
import json

import pytest

from scripts.recover_tai_focused_archives import (
    DATASETS,
    METHODS,
    REQUIRED_FILES,
    artifact_names,
    recover,
)


def _archives(tmp_path, seed=71):
    metrics_root = tmp_path / "metrics"
    models_root = tmp_path / "models"
    for dataset in DATASETS:
        for method in METHODS:
            metrics_name, models_name, checksum_name = artifact_names(
                seed, dataset, method
            )
            metric = metrics_root / metrics_name
            cell = metric / "results" / dataset.lower() / "dirichlet-0.1"
            cell = cell / str(seed) / method
            cell.mkdir(parents=True)
            for name in REQUIRED_FILES:
                (cell / name).write_bytes(b"synthetic-fixture-only")
            model = models_root / models_name / "global_model.pt"
            model.parent.mkdir(parents=True)
            blob = f"fixture:{seed}:{dataset}:{method}".encode()
            model.write_bytes(blob)
            rel = f"{dataset.lower()}/dirichlet-0.1/{seed}/{method}/global_model.pt"
            digest = hashlib.sha256(blob).hexdigest()
            if checksum_name == "model-sha256.json":
                manifest = {rel: digest}
            else:
                manifest = {rel: {"sha256": digest, "bytes": len(blob)}}
            (metric / checksum_name).write_text(json.dumps(manifest))
    return metrics_root, models_root


def test_six_original_checkpoint_archives_recover_without_metric_claim(tmp_path):
    metrics, models = _archives(tmp_path)
    records = recover(metrics, models, 71)
    assert len(records) == 6
    assert {(x["dataset"], x["method"]) for x in records} == {
        (dataset, method) for dataset in DATASETS for method in METHODS
    }
    for record in records:
        assert len(record["checkpoint_digests"]) == 1


def test_seed53_legacy_fedavg_manifest_is_verified(tmp_path):
    metrics, models = _archives(tmp_path, seed=53)
    assert len(recover(metrics, models, 53)) == 6


def test_checkpoint_tampering_rejected(tmp_path):
    metrics, models = _archives(tmp_path)
    name = artifact_names(71, "CIFAR10", "fedavg")[1]
    (models / name / "global_model.pt").write_bytes(b"altered")
    with pytest.raises(ValueError, match="checkpoint"):
        recover(metrics, models, 71)


def test_missing_original_sidecar_rejected(tmp_path):
    metrics, models = _archives(tmp_path)
    name = artifact_names(71, "CIFAR10", "ditto")[0]
    path = metrics / name / "results" / "cifar10"
    path = path / "dirichlet-0.1" / "71" / "ditto" / "rounds.csv"
    path.unlink()
    with pytest.raises(FileNotFoundError, match="required raw evidence"):
        recover(metrics, models, 71)


def test_missing_original_checkpoint_archive_rejected(tmp_path):
    metrics, models = _archives(tmp_path)
    name = artifact_names(71, "CIFAR100", "per_fedavg")[1]
    (models / name / "global_model.pt").unlink()
    with pytest.raises(ValueError, match="absent checkpoint"):
        recover(metrics, models, 71)


def test_archive_path_traversal_rejected(tmp_path):
    metrics, models = _archives(tmp_path)
    name, _, manifest = artifact_names(71, "CIFAR10", "fedavg")
    (metrics / name / manifest).write_text(
        json.dumps({"../../outside.pt": {"sha256": "a" * 64, "bytes": 1}})
    )
    with pytest.raises(ValueError, match="untrusted checkpoint path"):
        recover(metrics, models, 71)
