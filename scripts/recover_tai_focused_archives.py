#!/usr/bin/env python3
"""Reconstruct original IEEE TAI focused CIFAR archives for independent audit.

No training, metric recomputation or inferential statistics are performed here.
Reject missing, ambiguous, or checkpoint-digest-mismatched evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath

DATASETS = ("CIFAR10", "CIFAR100")
METHODS = ("fedavg", "ditto", "per_fedavg")
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REQUIRED_FILES = (
    "result.json",
    "request.json",
    "rounds.csv",
    "clients_deployed.csv",
    "clients_global.csv",
    "train_indices.npz",
    "test_indices.npz",
)


def artifact_names(seed: int, dataset: str, method: str) -> tuple[str, str, str]:
    if seed == 53 and method == "fedavg":
        prefix = "tai-seed53-"
        return (
            f"{prefix}metrics-{dataset}-{method}",
            f"{prefix}models-{dataset}-{method}",
            "model-sha256.json",
        )
    suffix = f"{dataset}-{method}-seed{seed}"
    return (
        f"ieee-tai-focus-evidence-{suffix}",
        f"ieee-tai-focus-models-{suffix}",
        "MODEL_SHA256.json",
    )


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def recover(metrics_root: Path, models_root: Path, seed: int) -> list[dict]:
    """Audit six checkpoint manifests, restore their byte-identical models."""
    if seed not in (53, 71):
        raise ValueError("only the separately planned seed-53/71 batches are supported")
    records = []
    for dataset in DATASETS:
        for method in METHODS:
            metrics_name, models_name, checksum_name = artifact_names(
                seed, dataset, method
            )
            metrics = metrics_root / metrics_name
            models = models_root / models_name
            if not metrics.is_dir() or not models.is_dir():
                raise FileNotFoundError(
                    f"required original archives missing: {metrics_name} / {models_name}"
                )
            manifest_path = metrics / checksum_name
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or not manifest:
                raise ValueError(f"invalid or empty model checksum list: {manifest_path}")

            prefix = f"{dataset.lower()}/dirichlet-0.1/{seed}/{method}/"
            cell = metrics / "results" / prefix
            for filename in REQUIRED_FILES:
                if not (cell / filename).is_file():
                    raise FileNotFoundError(f"required raw evidence missing: {cell / filename}")
            original_results = list(metrics.rglob("result.json"))
            if len(original_results) != 1 or original_results[0] != cell / "result.json":
                raise ValueError(f"unexpected or duplicate result.json in {metrics_name}")

            verified_models = []
            for rel, supplied in sorted(manifest.items()):
                if not isinstance(rel, str):
                    raise ValueError("invalid non-string model path")
                posix_path = PurePosixPath(rel)
                if (
                    not rel.startswith(prefix)
                    or posix_path.is_absolute()
                    or ".." in posix_path.parts
                    or posix_path.suffix != ".pt"
                ):
                    raise ValueError(f"untrusted checkpoint path: {rel}")
                expected_sha = (
                    supplied if isinstance(supplied, str)
                    else supplied.get("sha256") if isinstance(supplied, dict)
                    else None
                )
                if not isinstance(expected_sha, str) or not HEX_SHA256.fullmatch(
                    expected_sha
                ):
                    raise ValueError(f"malformed SHA-256 for checkpoint {rel}")
                matches = list(models.rglob(posix_path.name))
                if len(matches) != 1:
                    raise ValueError(f"ambiguous or absent checkpoint {rel}: {matches}")
                source = matches[0]
                if isinstance(supplied, dict) and source.stat().st_size != supplied.get(
                    "bytes"
                ):
                    raise ValueError(f"checkpoint byte-length mismatch: {rel}")
                if sha256_file(source) != expected_sha:
                    raise ValueError(f"checkpoint SHA-256 mismatch: {rel}")
                destination = metrics / "results" / Path(*posix_path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    if sha256_file(destination) != expected_sha:
                        raise ValueError(f"conflicting existing checkpoint: {destination}")
                else:
                    shutil.copyfile(source, destination)
                    if sha256_file(destination) != expected_sha:
                        raise ValueError(f"checkpoint changed during copy: {rel}")
                verified_models.append({"path": rel, "sha256": expected_sha})
            records.append(
                {
                    "seed": seed,
                    "dataset": dataset,
                    "method": method,
                    "metrics_archive": metrics_name,
                    "models_archive": models_name,
                    "checkpoint_digests": verified_models,
                }
            )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metrics_root", type=Path)
    parser.add_argument("models_root", type=Path)
    parser.add_argument("--seed", type=int, required=True, choices=(53, 71))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    records = recover(args.metrics_root, args.models_root, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"status": "CHECKPOINT_HASHED_NOT_YET_RAW_AUDITED",
                    "seed": args.seed, "records": records},
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Recovered {len(records)} model/checkpoint-matched seed {args.seed} cells")


if __name__ == "__main__":
    main()
