"""Tests for the independent-seed evidence gate."""
import unittest
from research.validation.paired_seed_evidence_gate import evaluate

def cell(method, seed, p10, dataset="CIFAR10"):
    return dict(dataset=dataset, method=method, seed=seed, rounds=50, clients=20,
                p10_accuracy=p10, source_sha="frozen", train_partition_hash="train",
                test_partition_hash="test")

class PairedSeedGateTests(unittest.TestCase):
    def test_partial_never_claims_inference(self):
        records = [cell("fedavg", 23, .2), cell("ditto", 23, .6)]
        out = evaluate(records, seeds=(23,149), bootstrap_reps=100)
        self.assertEqual(out["inferential_gate"], "closed")
        self.assertNotIn("exact_two_sided_p", out["comparisons"][0])
    def test_two_seed_exact_sign_flip(self):
        records = [cell(method, seed, p) for seed in (23,149)
                   for method,p in (("fedavg",.2),("ditto",.6))]
        out = evaluate(records, seeds=(23,149), bootstrap_reps=100)
        self.assertEqual(out["comparisons"][0]["exact_two_sided_p"], .5)
        self.assertNotIn("holm_adjusted_p", out["comparisons"][0])
    def test_duplicate_rejected(self):
        r = cell("fedavg",23,.2)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            evaluate([r,r], seeds=(23,), bootstrap_reps=100)
    def test_partition_mismatch_rejected(self):
        a = cell("fedavg",23,.2)
        b = cell("ditto",23,.5)
        b["test_partition_hash"] = "other"
        with self.assertRaisesRegex(ValueError, "Mismatched"):
            evaluate([a,b], seeds=(23,), bootstrap_reps=100)
    def test_one_round_pilot_rejected(self):
        r = cell("fedavg",23,.2)
        r["rounds"] = 1
        with self.assertRaisesRegex(ValueError, "50-round"):
            evaluate([r], seeds=(23,), bootstrap_reps=100)

if __name__ == "__main__":
    unittest.main()
