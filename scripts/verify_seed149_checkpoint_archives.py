#!/usr/bin/env python3
"""Verify the original GitHub Actions seed-149 checkpoint ZIPs before durable archival.

Only verifies ZIP bytes, not scientific training validity. No ledger cells are credited.
Usage:
 python scripts/verify_seed149_checkpoint_archives.py /path/to/checkpoints
Expected filenames: <artifact-id>.zip or seed149_checkpoint_<artifact-id>.zip.
"""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile

# Immutable digest receipts from original GitHub Actions run 43731822341.
# Source: https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/runs/43731822341
ARTIFACTS = {
    14170836121: ("CIFAR10", "ditto", 170765658, "fbe668857a7f0a637a61eb4a0e603a21743dae7a5cd17038a23763963be42bfd"),
    14171984385: ("CIFAR10", "per_fedavg", 170756692, "2a894612acd8a73052249e3e69a948515040b9f941f3e56a033910ef481cbd49"),
    14175164182: ("CIFAR100", "ditto", 172548061, "43f1f134281a016515d1ae4e02bdbf09b62471e4d4457fcdee1755ab4c22f015"),
    14176879700: ("CIFAR100", "per_fedavg", 172545947, "dba5f245e81915c6ac00f97877c1415713af9051a4bb1693a4c88cf653b8072b"),
}

def audit(path: Path, expected_size: int, expected_digest: str) -> dict:
    result = {"path": str(path), "passed": False}
    if not path.is_file():
        result["error"] = "missing archive"
        return result
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    result.update(size_bytes=size, sha256=digest.hexdigest())
    if size != expected_size or digest.hexdigest() != expected_digest:
        result["error"] = "GitHub receipt size/digest mismatch"
        return result
    try:
        with ZipFile(path) as archive:
            names = archive.namelist()
            if not names:
                result["error"] = "empty ZIP"
                return result
            corrupted = archive.testzip()
            if corrupted is not None:
                result["error"] = f"ZIP CRC mismatch: {corrupted}"
                return result
            result["members"] = len(names)
    except (BadZipFile, OSError, RuntimeError) as exc:
        result["error"] = f"ZIP verification failed: {exc}"
        return result
    result["passed"] = True
    return result

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, help="Write JSON receipt to this path")
    args = parser.parse_args()
    checks = []
    for artifact_id, (dataset, method, size, digest) in ARTIFACTS.items():
        candidates = [
            args.directory / f"{artifact_id}.zip",
            args.directory / f"seed149_checkpoint_{artifact_id}.zip",
        ]
        if artifact_id == 14170836121:
            candidates.append(args.directory / "seed149_cifar10_ditto_checkpoint.zip")
        path = next((p for p in candidates if p.is_file()), candidates[0])
        checks.append({"artifact_id": artifact_id, "dataset": dataset,
                       "method": method, **audit(path, size, digest)})
    report = {
        "source_run": 43731822341,
        "scope": "archive transport integrity only; not model-level or training validation",
        "all_passed": all(item["passed"] for item in checks),
        "checks": checks,
    }
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["all_passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
