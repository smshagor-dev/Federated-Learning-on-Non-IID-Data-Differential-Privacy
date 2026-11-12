"""Statistical-method tests use constructed numbers, never research observations."""
import pytest
from scripts.tai_confirmatory_inference import SEEDS, DATASETS, METHODS, analyze, exact_signflip, holm

def test_exact_two_sided_signflip_ten_positive():
    assert exact_signflip([0.1]*10) == 2/1024

def test_symmetric_zero_effect():
    assert exact_signflip([0.1, -0.1]) == 1.0

def test_holm_familywise_monotonicity():
    assert holm({"a": .001, "b": .02, "c": .015, "d": .2}) == {
        "a": .004, "c": .045, "b": .045, "d": .2}

def test_one_seed_never_reports_inferential_claim():
    rows = {(d, 11, m): (.3+i*.01, "a"*40, "b"*64, "c"*64, "d"*64)
            for d in DATASETS for i, m in enumerate(METHODS)}
    result = analyze(rows, draws=1000)
    assert result["status"] == "INSUFFICIENT_PAIRED_SEEDS_NO_PVALUES"
    assert all("exact_two_sided_signflip_p" not in t for t in result["tests"].values())

def test_complete_four_test_family_and_adjustment():
    rows = {(d, seed, m): (.3+i*.1, "a"*40, "b"*64, "c"*64, "d"*64)
            for d in DATASETS for seed in SEEDS for i,m in enumerate(METHODS)}
    result = analyze(rows, draws=1000)
    assert result["status"] == "FOUR_CONFIRMATORY_TESTS_COMPLETE_NOT_FULL_STUDY"
    assert all(t["holm_adjusted_p"] == 8/1024 for t in result["tests"].values())

def test_unmatched_source_rejected():
    rows = {("CIFAR10", 11, "fedavg"): (.3, "a"*40, "b"*64, "e"*64, "f"*64),
            ("CIFAR10", 11, "ditto"): (.5, "c"*40, "d"*64, "e"*64, "f"*64)}
    with pytest.raises(ValueError, match="unmatched source"):
        analyze(rows)


def test_unmatched_train_partition_rejected():
    rows = {("CIFAR10", 11, "fedavg"): (.3, "a"*40, "b"*64, "c"*64, "d"*64),
            ("CIFAR10", 11, "ditto"): (.5, "a"*40, "e"*64, "f"*64, "d"*64)}
    with pytest.raises(ValueError, match="partition hashes"):
        analyze(rows)


def test_unmatched_test_partition_rejected():
    rows = {("CIFAR100", 11, "fedavg"): (.3, "a"*40, "b"*64, "c"*64, "d"*64),
            ("CIFAR100", 11, "per_fedavg"): (.5, "a"*40, "e"*64, "c"*64, "f"*64)}
    with pytest.raises(ValueError, match="partition hashes"):
        analyze(rows)


def test_legacy_csv_without_hashes_cannot_support_inference(tmp_path):
    from scripts.tai_confirmatory_inference import load_rows, FROZEN_SOURCE_SHA
    p = tmp_path / "legacy.csv"
    p.write_text("evidence_type,dataset,condition,method,seed,adaptation_steps,p10_client_accuracy,source_commit_sha,run_id\n"
                 + "50-round-primary,CIFAR10,dirichlet:0.1,fedavg,11,1,0.3," + FROZEN_SOURCE_SHA + "," + "b"*64 + "\n")
    with pytest.raises(ValueError, match="partition hashes"):
        load_rows([p])


def test_inference_csv_rejects_reused_run_id_across_seeds(tmp_path):
    import csv
    from scripts.tai_confirmatory_inference import load_rows, FROZEN_SOURCE_SHA
    path = tmp_path / "reused.csv"
    fields = ("evidence_type", "dataset", "condition", "method", "seed",
              "adaptation_steps", "p10_client_accuracy", "source_commit_sha",
              "run_id", "train_partition_hash", "test_partition_hash")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for seed in (11, 23):
            writer.writerow(dict(evidence_type="50-round-primary", dataset="CIFAR10",
                                 condition="dirichlet:0.1", method="fedavg", seed=seed,
                                 adaptation_steps=1, p10_client_accuracy=.25,
                                 source_commit_sha=FROZEN_SOURCE_SHA,
                                 run_id="a"*64, train_partition_hash="b"*64,
                                 test_partition_hash="c"*64))
    with pytest.raises(ValueError, match="run ID reused"):
        load_rows([path])


def test_inference_csv_rejects_nonfrozen_source_and_nonhex_hash(tmp_path):
    import csv
    from scripts.tai_confirmatory_inference import load_rows, FROZEN_SOURCE_SHA
    path = tmp_path / "source.csv"
    fields = ("evidence_type", "dataset", "condition", "method", "seed",
              "adaptation_steps", "p10_client_accuracy", "source_commit_sha",
              "run_id", "train_partition_hash", "test_partition_hash")
    row = dict(evidence_type="50-round-primary", dataset="CIFAR10",
               condition="dirichlet:0.1", method="fedavg", seed=11,
               adaptation_steps=1, p10_client_accuracy=.25,
               source_commit_sha="a"*40, run_id="b"*64,
               train_partition_hash="c"*64, test_partition_hash="d"*64)
    def write():
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow(row)
    write()
    with pytest.raises(ValueError, match="immutable training source"):
        load_rows([path])
    row["source_commit_sha"] = FROZEN_SOURCE_SHA
    row["train_partition_hash"] = "z"*64
    write()
    with pytest.raises(ValueError, match="malformed"):
        load_rows([path])
