"""Regression tests: never silently count divergent evidence with a reused run ID."""

import unittest

from scripts.audit_tai_primary_collection import _same_duplicate_result


class DuplicateEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.result = {
            "run_id": "same-id",
            "request": {"seed": 23, "train_partition_hash": "partition-a"},
            "metrics": {"p10_client_accuracy": 0.24, "client_count": 20},
        }

    def test_identical_copy_is_duplicate(self):
        self.assertTrue(_same_duplicate_result(self.result, dict(self.result)))

    def test_reused_run_id_with_modified_metric_is_conflict(self):
        changed = {
            **self.result,
            "metrics": {**self.result["metrics"], "p10_client_accuracy": 0.99},
        }
        self.assertFalse(_same_duplicate_result(self.result, changed))

    def test_reused_run_id_with_modified_partition_is_conflict(self):
        changed = {
            **self.result,
            "request": {
                **self.result["request"],
                "train_partition_hash": "partition-b",
            },
        }
        self.assertFalse(_same_duplicate_result(self.result, changed))


if __name__ == "__main__":
    unittest.main()
