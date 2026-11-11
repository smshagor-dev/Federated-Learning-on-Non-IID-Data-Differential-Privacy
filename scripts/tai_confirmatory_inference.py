#!/usr/bin/env python3
"""Evidence-gated paired inference; input must be independently audited primary CSV."""
import argparse
import csv
import itertools
import json
import math
import random
import re
from pathlib import Path

SEEDS = (11, 23, 37, 53, 71, 83, 97, 109, 127, 149)
DATASETS = ("CIFAR10", "CIFAR100")
METHODS = ("fedavg", "ditto", "per_fedavg")
METRIC = "p10_client_accuracy"
FROZEN_SOURCE_SHA = "a6641509c2b8e6557101499db553177d70a6a3e2"
HEX_64 = re.compile(r"[0-9a-f]{64}\Z")

def load_rows(paths):
    records = {}
    run_owners = {}
    for path in paths:
        with open(path, newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                if row.get("evidence_type") != "50-round-primary":
                    continue
                dataset, method = row["dataset"], row["method"]
                if dataset not in DATASETS or row["condition"] != "dirichlet:0.1" or method not in METHODS:
                    continue
                seed = int(row["seed"])
                if seed not in SEEDS or int(row["adaptation_steps"]) != 1:
                    raise ValueError("unprespecified primary cell")
                value = float(row[METRIC])
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError("invalid held-out p10 accuracy")
                sha, run = row["source_commit_sha"], row["run_id"]
                train_hash, test_hash = row.get("train_partition_hash", ""), row.get("test_partition_hash", "")
                if sha != FROZEN_SOURCE_SHA:
                    raise ValueError("unmatched immutable training source SHA")
                if not all(HEX_64.fullmatch(x) for x in (run, train_hash, test_hash)):
                    raise ValueError("missing or malformed run ID or audited partition hashes")
                key = (dataset, seed, method)
                owner = run_owners.setdefault(run, key)
                if owner != key:
                    raise ValueError(f"run ID reused across distinct experimental cells: {owner} and {key}")
                item = (value, sha, run, train_hash, test_hash)
                if key in records and records[key] != item:
                    raise ValueError(f"conflicting duplicate result: {key}")
                records[key] = item
    return records

def exact_signflip(values):
    if len(values) < 2:
        raise ValueError("insufficient independent paired seeds")
    observed = abs(sum(values))
    extreme = sum(abs(sum(s*x for s, x in zip(signs, values))) >= observed - 1e-12
                  for signs in itertools.product((-1, 1), repeat=len(values)))
    return extreme / (2 ** len(values))

def bootstrap_ci(values, draws=20000, seed=20261111):
    rng = random.Random(seed)
    n = len(values)
    samples = sorted(sum(values[rng.randrange(n)] for _ in range(n))/n for _ in range(draws))
    return [samples[int(.025*draws)], samples[min(draws-1, math.ceil(.975*draws)-1)]]

def holm(pvalues):
    adjusted, running = {}, 0.0
    for index, key in enumerate(sorted(pvalues, key=pvalues.get)):
        running = max(running, min(1.0, (len(pvalues)-index)*pvalues[key]))
        adjusted[key] = running
    return adjusted

def analyze(records, draws=20000):
    tests = {}
    for dataset in DATASETS:
        for method in METHODS[1:]:
            key = f"{dataset}:{method}-fedavg:p10"
            pairs = []
            for seed in SEEDS:
                a = records.get((dataset, seed, method))
                b = records.get((dataset, seed, "fedavg"))
                if a is None or b is None:
                    continue
                if a[1] != b[1]:
                    raise ValueError(f"unmatched source SHA: {dataset}/{seed}/{method}")
                if len(a) < 5 or len(b) < 5 or a[3:5] != b[3:5] or not all(len(h) == 64 for h in a[3:5] + b[3:5]):
                    raise ValueError(f"unmatched or missing train/test partition hashes: {dataset}/{seed}/{method}")
                pairs.append((seed, a[0]-b[0]))
            entry = {"paired_seed_count": len(pairs),
                     "paired_seeds": [s for s, _ in pairs],
                     "missing_seeds": [s for s in SEEDS if s not in dict(pairs)],
                     "status": "COMPLETE" if len(pairs) == 10 else "INCOMPLETE_NOT_INFERENTIAL"}
            if len(pairs) == 10:
                diffs = [x for _, x in pairs]
                entry["mean_effect_percentage_points"] = 100*sum(diffs)/10
                entry["exact_two_sided_signflip_p"] = exact_signflip(diffs)
                entry["paired_bootstrap_95ci_percentage_points"] = [100*x for x in bootstrap_ci(diffs, draws)]
            tests[key] = entry
    if all(x["status"] == "COMPLETE" for x in tests.values()):
        for key, value in holm({k: v["exact_two_sided_signflip_p"] for k,v in tests.items()}).items():
            tests[key]["holm_adjusted_p"] = value
            tests[key]["reject_at_familywise_0_05"] = value <= .05
        status = "FOUR_CONFIRMATORY_TESTS_COMPLETE_NOT_FULL_STUDY"
    else:
        status = "INSUFFICIENT_PAIRED_SEEDS_NO_PVALUES"
    return {"status": status, "unit_of_replication": "independent training seed",
            "planned_seeds_per_hypothesis": 10, "familywise_alpha": .05,
            "bootstrap_draws": draws, "tests": tests,
            "warning": "CSV is not independently verified by this script: first run the raw-archive primary collection audit."}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ledgers", nargs="+", help="audited primary collection CSV")
    parser.add_argument("--output", default="tai_confirmatory_inference.json")
    parser.add_argument("--bootstrap-draws", type=int, default=20000)
    args = parser.parse_args()
    if args.bootstrap_draws < 1000:
        parser.error("minimum 1000 bootstrap draws")
    result = analyze(load_rows(args.ledgers), args.bootstrap_draws)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "paired_seeds": {k: v["paired_seed_count"] for k,v in result["tests"].items()}}, indent=2))

if __name__ == "__main__":
    main()
