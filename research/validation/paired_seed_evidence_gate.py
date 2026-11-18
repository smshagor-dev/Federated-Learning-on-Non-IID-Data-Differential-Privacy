"""Evidence-gated exact paired seed-level P10 inference. No CI/pilot imputation."""
import itertools
import math
import random

SEEDS = (11, 23, 37, 53, 71, 83, 97, 109, 127, 149)
DATASETS = ("CIFAR10", "CIFAR100")
METHODS = ("ditto", "per_fedavg")

def evaluate(records, seeds=SEEDS, bootstrap_reps=10000):
    """Records: dataset, method, seed, rounds, clients, p10_accuracy,
    source_sha, train_partition_hash, test_partition_hash.
    Only fully matched 10-seed comparisons produce inferential statistics.
    """
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("Seed list must be nonempty and unique")
    index = {}
    for r in records:
        key = (r["dataset"], r["method"], int(r["seed"]))
        if key in index:
            raise ValueError("Duplicate cell: %r" % (key,))
        if r["rounds"] != 50 or r["clients"] != 20:
            raise ValueError("Not a verified 50-round/20-client cell: %r" % (key,))
        p10 = float(r["p10_accuracy"])
        if not math.isfinite(p10) or not 0 <= p10 <= 1:
            raise ValueError("Invalid P10: %r" % (key,))
        if not all(r.get(k) for k in ("source_sha", "train_partition_hash", "test_partition_hash")):
            raise ValueError("Missing provenance: %r" % (key,))
        index[key] = r
    out = []
    for dataset in DATASETS:
        for method in METHODS:
            pairs = []
            for seed in seeds:
                baseline = index.get((dataset, "fedavg", seed))
                treatment = index.get((dataset, method, seed))
                if baseline is None or treatment is None:
                    continue
                for field in ("source_sha", "train_partition_hash", "test_partition_hash"):
                    if baseline[field] != treatment[field]:
                        raise ValueError("Mismatched %s: %s/%s/%s" % (field, dataset, method, seed))
                pairs.append((seed, float(treatment["p10_accuracy"]) - float(baseline["p10_accuracy"])))
            result = dict(dataset=dataset, method=method, matched_seeds=[s for s, _ in pairs],
                          n=len(pairs), complete=len(pairs) == len(seeds))
            if result["complete"]:
                diffs = [d for _, d in pairs]
                observed = abs(sum(diffs))
                extreme = sum(abs(sum(sign*d for sign, d in zip(signs, diffs))) >= observed - 1e-12
                              for signs in itertools.product((-1, 1), repeat=len(diffs)))
                result["mean_p10_gain_pp"] = 100*sum(diffs)/len(diffs)
                result["exact_two_sided_p"] = extreme/(2**len(diffs))
                rng = random.Random(20261118 + 2*DATASETS.index(dataset) + METHODS.index(method))
                draws = sorted(sum(rng.choice(diffs) for _ in diffs)/len(diffs)
                               for _ in range(bootstrap_reps))
                result["paired_bootstrap_95ci_pp"] = [
                    100*draws[int(.025*bootstrap_reps)],
                    100*draws[min(bootstrap_reps-1, math.ceil(.975*bootstrap_reps)-1)]]
            out.append(result)
    complete = sorted((r for r in out if r["complete"]), key=lambda r: r["exact_two_sided_p"])
    if len(complete) == 4:
        running = 0
        for i, r in enumerate(complete):
            running = max(running, min(1, r["exact_two_sided_p"]*(4-i)))
            r["holm_adjusted_p"] = running
    return dict(inferential_gate="open" if len(complete) == 4 else "closed", comparisons=out)
