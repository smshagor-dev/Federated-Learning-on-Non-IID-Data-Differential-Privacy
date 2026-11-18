"""Tests for the independent-seed evidence gate."""

import unittest

from research.validation.paired_seed_evidence_gate import evaluate


def cell(method, seed, p10, dataset="CIFAR10"):
    return {
        "dataset": dataset,
        "method": method,
        "seed": seed,
        "rounds": 50,
        "clients": 20,
        "p10_accuracy": p10,
        "source_sha": "frozen",
        "train_partition_hash": "train",
        "test_partition_hash": "test",
    }


class PairedSeedGateTests(unittest.TestCase):
    def test_partial_never_claims_inference(self):
        records = [cell("fedavg", 23, 0.2), cell("ditto", 23, 0.6)]
        out = evaluate(records, seeds=(23, 149), bootstrap_reps=100)
        self.assertEqual(out["inferential_gate"], "closed")
        self.assertNotIn("exact_two_sided_p", out["comparisons"][0])

    def test_two_seed_exact_sign_flip(self):
        records = [
            cell(method, seed, p)
            for seed in (23, 149)
            for method, p in (("fedavg", 0.2), ("ditto", 0.6))
        ]
        out = evaluate(records, seeds=(23, 149), bootstrap_reps=100)
        self.assertEqual(out["comparisons"][0]["exact_two_sided_p"], 0.5)
        self.assertNotIn("holm_adjusted_p", out["comparisons"][0])

    def test_duplicate_rejected(self):
        record = cell("fedavg", 23, 0.2)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            evaluate([record, record], seeds=(23,), bootstrap_reps=100)

    def test_partition_mismatch_rejected(self):
        baseline = cell("fedavg", 23, 0.2)
        treatment = cell("ditto", 23, 0.5)
        treatment["test_partition_hash"] = "other"
        with self.assertRaisesRegex(ValueError, "Mismatched"):
            evaluate([baseline, treatment], seeds=(23,), bootstrap_reps=100)

    def test_one_round_pilot_rejected(self):
        record = cell("fedavg", 23, 0.2)
        record["rounds"] = 1
        with self.assertRaisesRegex(ValueError, "50-round"):
            evaluate([record], seeds=(23,), bootstrap_reps=100)


if __name__ == "__main__":
    unittest.main()
