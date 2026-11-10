"""Synthetic unit-test fixtures; NEVER journal-result evidence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import TensorDataset

from tai_pfl.analysis import (
    _check_cell,
    analyze,
    exact_paired_sign_flip,
    holm_adjust,
)
from tai_pfl.engine import (
    METHODS,
    TrainSpec,
    _support_query,
    average_states,
    summarize_client_rows,
)
from tai_pfl.heterogeneity import realized_heterogeneity
from tai_pfl.study import run_cell


class RealDataStudyContractTests(unittest.TestCase):
    @staticmethod
    def fixture() -> tuple[TensorDataset, TensorDataset]:
        gen = torch.Generator().manual_seed(11)
        x_train = torch.randn(16, 1, 2, 2, generator=gen)
        y_train = (x_train.flatten(1).sum(1) > 0).long()
        x_test = torch.randn(8, 1, 2, 2, generator=gen)
        y_test = (x_test.flatten(1).sum(1) > 0).long()
        return TensorDataset(x_train, y_train), TensorDataset(x_test, y_test)

    @staticmethod
    def factory() -> nn.Module:
        return nn.Sequential(nn.Flatten(), nn.Linear(4, 2))

    def test_all_six_methods_use_real_local_indices_not_synthetic_worker(self) -> None:
        torch.set_num_threads(1)
        train, test = self.fixture()
        train_p = {0: np.arange(0, 8), 1: np.arange(8, 16)}
        test_p = {0: np.arange(0, 4), 1: np.arange(4, 8)}
        spec = TrainSpec(
            batch_size=4,
            local_epochs=1,
            participation=1.0,
            learning_rate=0.03,
            adaptation_steps=2,
        )
        with tempfile.TemporaryDirectory() as tmp:
            for method in METHODS:
                result = run_cell(
                    train_dataset=train,
                    test_dataset=test,
                    train_partitions=train_p,
                    test_partitions=test_p,
                    model_factory=self.factory,
                    method=method,
                    spec=spec,
                    rounds=1,
                    seed=11,
                    output_dir=Path(tmp) / method,
                    dataset_id="synthetic-fixture",
                    partition_id="iid",
                    source_sha="synthetic-test-only",
                    official_torchvision=False,
                )
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["metrics"]["client_count"], 2)
                self.assertEqual(
                    result["evidence_class"], "synthetic_unit_test_not_publishable"
                )
                self.assertTrue((Path(tmp) / method / "global_model.pt").exists())
                if method in ("ditto", "per_fedavg"):
                    self.assertEqual(
                        result["primary_evaluation"], "personalized_deployed"
                    )
            with self.assertRaisesRegex(ValueError, "not publishable"):
                _check_cell(Path(tmp) / "fedavg" / "result.json")
            with self.assertRaisesRegex(ValueError, "submission evidence gate"):
                analyze(Path(tmp) / "missing")

    def test_support_query_no_training_overlap(self) -> None:
        indices = np.arange(12)
        left, right = _support_query(indices, 20)
        self.assertFalse(set(left) & set(right))
        self.assertEqual(set(np.concatenate([left, right])), set(indices))

    def test_realized_heterogeneity_identifies_label_support_and_size_skew(self) -> None:
        train, _test = self.fixture()
        indices = {0: np.arange(4), 1: np.arange(4, 16)}
        stats = realized_heterogeneity(train, indices)
        self.assertEqual(stats["client_count"], 2)
        self.assertEqual(stats["minimum_train_samples"], 4)
        self.assertEqual(stats["maximum_train_samples"], 12)
        self.assertGreater(stats["training_client_size_cv"], 0)
        self.assertGreaterEqual(stats["mean_pairwise_js_divergence"], 0)
        self.assertLessEqual(stats["mean_pairwise_js_divergence"], 1)
        self.assertEqual(sum(row["sample_count"] for row in stats["clients"]), 16)

    def test_partition_validation_refuses_missing_heldout_data(self) -> None:
        train, test = self.fixture()
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaisesRegex(ValueError, "exactly once"),
        ):
            run_cell(
                train_dataset=train,
                test_dataset=test,
                train_partitions={0: np.arange(8), 1: np.arange(8, 16)},
                test_partitions={0: np.arange(4), 1: np.arange(4, 7)},
                model_factory=self.factory,
                method="fedavg",
                spec=TrainSpec(),
                rounds=1,
                seed=11,
                output_dir=Path(tmp) / "bad",
                dataset_id="fixture",
                partition_id="iid",
                source_sha="test",
                official_torchvision=False,
            )

    def test_aggregation_mean_and_fairness(self) -> None:
        base = {"w": torch.tensor([0.0, 2.0])}
        result = average_states(
            base,
            [
                {"w": torch.tensor([2.0, 4.0])},
                {"w": torch.tensor([4.0, 6.0])},
            ],
        )
        self.assertTrue(torch.allclose(result["w"], torch.tensor([3.0, 5.0])))
        summary = summarize_client_rows(
            [
                {"accuracy": 0.5, "n_test": 10},
                {"accuracy": 1.0, "n_test": 20},
            ]
        )
        self.assertAlmostEqual(summary["micro_accuracy"], 25 / 30)
        self.assertAlmostEqual(summary["worst_client_accuracy"], 0.5)
        self.assertLess(summary["jain_accuracy_index"], 1.0)

    def test_exact_five_seed_sign_flip_cannot_reach_five_percent(self) -> None:
        self.assertAlmostEqual(exact_paired_sign_flip(np.ones(5)), 0.0625)
        self.assertAlmostEqual(exact_paired_sign_flip(np.ones(10)), 2 / 1024)

    def test_holm_correction_is_monotone_and_bounded(self) -> None:
        adjusted = holm_adjust([0.04, 0.01, 0.2])
        self.assertEqual(adjusted, [0.08, 0.03, 0.2])

    def test_no_reuse_of_incomplete_or_unaudited_runs(self) -> None:
        train, test = self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            kwargs = {
                "train_dataset": train,
                "test_dataset": test,
                "train_partitions": {0: np.arange(8), 1: np.arange(8, 16)},
                "test_partitions": {0: np.arange(4), 1: np.arange(4, 8)},
                "model_factory": self.factory,
                "method": "fedavg",
                "spec": TrainSpec(local_epochs=1, participation=1.0),
                "rounds": 1,
                "seed": 11,
                "output_dir": Path(tmp) / "fedavg",
                "dataset_id": "fixture",
                "partition_id": "iid",
                "source_sha": "test",
                "official_torchvision": False,
            }
            first = run_cell(**kwargs)
            second = run_cell(**kwargs)
            self.assertEqual(first["run_id"], second["run_id"])
            changed = dict(kwargs)
            changed["seed"] = 23
            with self.assertRaises(FileExistsError):
                run_cell(**changed)
            original = Path(tmp) / "fedavg" / "request.json"
            request = json.loads(original.read_text(encoding="utf-8"))
            request["seed"] = 55
            original.write_text(json.dumps(request), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not publishable"):
                _check_cell(Path(tmp) / "fedavg" / "result.json")


if __name__ == "__main__":
    unittest.main()
