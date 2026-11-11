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
    rows = {(d, 11, m): (.3+i*.01, "a"*40, "b"*64)
            for d in DATASETS for i, m in enumerate(METHODS)}
    result = analyze(rows, draws=1000)
    assert result["status"] == "INSUFFICIENT_PAIRED_SEEDS_NO_PVALUES"
    assert all("exact_two_sided_signflip_p" not in t for t in result["tests"].values())

def test_complete_four_test_family_and_adjustment():
    rows = {(d, seed, m): (.3+i*.1, "a"*40, "b"*64)
            for d in DATASETS for seed in SEEDS for i,m in enumerate(METHODS)}
    result = analyze(rows, draws=1000)
    assert result["status"] == "FOUR_CONFIRMATORY_TESTS_COMPLETE_NOT_FULL_STUDY"
    assert all(t["holm_adjusted_p"] == 8/1024 for t in result["tests"].values())

def test_unmatched_source_rejected():
    rows = {("CIFAR10", 11, "fedavg"): (.3, "a"*40, "b"*64),
            ("CIFAR10", 11, "ditto"): (.5, "c"*40, "d"*64)}
    with pytest.raises(ValueError, match="unmatched source"):
        analyze(rows)
