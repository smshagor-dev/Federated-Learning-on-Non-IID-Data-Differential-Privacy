"""Fail-closed pre-analysis manifest checks; NOT independent evidence verification.

The expected digest must be independently pinned by the operator after auditing
raw artifacts. A self-attested manifest, even with a matching digest, is NOT proof.
"""
import hashlib
import json
import re
from pathlib import Path

SEEDS = (11, 23, 37, 53, 71, 83, 97, 109, 127, 149)
COMPARISONS = frozenset(
    f"{dataset}:{method}:vs:fedavg"
    for dataset in ("CIFAR10", "CIFAR100")
    for method in ("ditto", "per_fedavg")
)

def load_verified_manifest(path, expected_sha256):
    raw = Path(path).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) or actual != expected_sha256.lower():
        raise ValueError("manifest digest not pinned or mismatch")
    doc = json.loads(raw)
    if doc.get("schema") != "tai-independent-evidence-v1":
        raise ValueError("unsupported evidence schema")
    if doc.get("auditor") in (None, "", "self-reported"):
        raise ValueError("independent auditor identifier required")
    rows = doc.get("paired_rows")
    if not isinstance(rows, list) or len(rows) != 40:
        raise ValueError("expected four comparisons x ten seeds")
    seen = set()
    # Both personalized methods must reuse one independently trained FedAvg
    # baseline for each dataset/seed; reject contradictory baseline receipts.
    baseline_by_dataset_seed = {}
    for row in rows:
        comparison, seed = row["comparison"], row["seed"]
        key = (comparison, seed)
        if comparison not in COMPARISONS or type(seed) is not int or seed not in SEEDS or key in seen:
            raise ValueError("unregistered comparison, invalid seed or duplicate")
        seen.add(key)
        dataset = comparison.split(":", 1)[0]
        baseline_key = (dataset, seed)
        baseline = row["baseline"]
        if baseline_key in baseline_by_dataset_seed and baseline_by_dataset_seed[baseline_key] != baseline:
            raise ValueError("inconsistent FedAvg baseline reused across comparisons")
        baseline_by_dataset_seed[baseline_key] = baseline
        for side in ("baseline", "treatment"):
            receipt = row[side]
            if receipt.get("independently_audited") is not True:
                raise ValueError("unaudited receipt")
            if receipt.get("round_ids") != list(range(1, 51)) or receipt.get("client_count") != 20:
                raise ValueError("incomplete rounds or clients")
            correct, total = receipt.get("correct"), receipt.get("total")
            if type(correct) is not int or type(total) is not int or total <= 0 or not 0 <= correct <= total:
                raise ValueError("invalid held-out counts")
            if not isinstance(receipt.get("artifact_sha256"), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", receipt["artifact_sha256"]):
                raise ValueError("missing artifact digest")
            if not isinstance(receipt.get("source_sha"), str) or not re.fullmatch(r"[0-9a-fA-F]{40}", receipt["source_sha"]):
                raise ValueError("missing immutable training source")
        for side in ("baseline", "treatment"):
            for partition_field in ("train_partition_sha", "test_partition_sha"):
                digest = row[side].get(partition_field)
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                    raise ValueError(f"invalid {partition_field} digest")
        for field in ("source_sha", "train_partition_sha", "test_partition_sha"):
            a, b = row["baseline"].get(field), row["treatment"].get(field)
            if not a or a != b:
                raise ValueError(f"pair provenance mismatch: {field}")
    if seen != {(name, seed) for name in COMPARISONS for seed in SEEDS}:
        raise ValueError("missing registered hypothesis/seed pair")
    return rows
