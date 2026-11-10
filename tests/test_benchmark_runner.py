from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_benchmark_matrix.py"
SPEC = importlib.util.spec_from_file_location("run_benchmark_matrix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
BenchmarkCell = runner.BenchmarkCell


class BenchmarkRunnerTests(unittest.TestCase):
    def base_config(self) -> dict:
        return {
            "system": {"seed": 42, "results_dir": "results", "device": "cpu"},
            "data": {
                "dataset": "CIFAR10",
                "partition": "dirichlet",
                "alpha": 0.1,
                "classes_per_client": 2,
                "quantity_skew_sigma": 1.0,
                "min_partition_size": 10,
            },
            "federated": {
                "num_clients": 10,
                "sample_rate": 0.2,
                "rounds": 50,
            },
            "algorithm": {"name": "fedprox"},
            "dp": {
                "enabled": True,
                "target_epsilon": 4.0,
                "target_delta": 1e-5,
                "noise_multiplier": 2.0,
            },
        }

    def cell(self, *, epsilon: float | None = 4.0):
        return BenchmarkCell(
            benchmark_id="bench",
            condition_id="condition",
            cell_id="cell-001",
            dataset_id="mnist",
            algorithm_id="fedavg",
            partition_name="quantity-skew-1",
            partition_strategy="quantity_skew",
            partition_parameters={"quantity_skew_sigma": 1.0},
            target_epsilon=epsilon,
            target_delta=1e-5 if epsilon is not None else None,
            seed=23,
            rounds=10,
            runtime_identity="root-simulator",
        )

    def test_cell_config_sets_all_runtime_dimensions(self) -> None:
        results_dir = Path("out") / "cell"
        config = runner._cell_config(self.base_config(), self.cell(), results_dir)
        self.assertEqual(config["system"]["seed"], 23)
        self.assertEqual(config["system"]["results_dir"], str(results_dir))
        self.assertEqual(config["data"]["dataset"], "MNIST")
        self.assertEqual(config["data"]["partition"], "quantity_skew")
        self.assertEqual(config["data"]["quantity_skew_sigma"], 1.0)
        self.assertEqual(config["algorithm"]["name"], "fedavg")
        self.assertEqual(config["federated"]["rounds"], 10)
        self.assertTrue(config["dp"]["enabled"])
        self.assertEqual(config["dp"]["target_epsilon"], 4.0)

    def test_non_private_cell_disables_dp(self) -> None:
        config = runner._cell_config(
            self.base_config(), self.cell(epsilon=None), Path("out")
        )
        self.assertFalse(config["dp"]["enabled"])
        self.assertIsNone(config["dp"]["target_epsilon"])

    def test_dry_run_writes_cell_config_without_launching_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, status = runner._run_cell(
                cell=self.cell(),
                base_config=self.base_config(),
                benchmark_root=root,
                resume=False,
                dry_run=True,
            )
            self.assertIsNone(summary)
            self.assertEqual(status, "planned")
            cell_root = root / "cells" / "cell-001"
            self.assertTrue((cell_root / "config.yaml").is_file())
            self.assertTrue((cell_root / "cell.json").is_file())
            persisted_config = yaml.safe_load(
                (cell_root / "config.yaml").read_text(encoding="utf-8")
            )
            persisted_cell = json.loads(
                (cell_root / "cell.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted_config["system"]["seed"], 23)
            self.assertEqual(persisted_cell["cell_id"], "cell-001")

    def test_summary_extraction_includes_heldout_client_metrics(self) -> None:
        summary = {
            "partition": {"partition_hash": "partition-sha"},
            "runs": [
                {
                    "algorithm": "fedavg",
                    "final_acc": 0.8,
                    "final_loss": 0.5,
                    "elapsed_sec": 12.0,
                    "mean_raw_drift": 1.0,
                    "mean_clipped_drift": 0.8,
                    "mean_fraction_clipped": 0.25,
                    "mean_aggregate_noise_norm": 0.1,
                    "final_epsilon": 3.9,
                    "client_evaluation": {
                        "mean_client_accuracy": 0.78,
                        "weighted_client_accuracy": 0.8,
                        "median_client_accuracy": 0.79,
                        "p10_client_accuracy": 0.6,
                        "worst_client_accuracy": 0.5,
                        "best_client_accuracy": 0.95,
                        "client_accuracy_std": 0.12,
                        "client_accuracy_range": 0.45,
                        "jain_accuracy_index": 0.97,
                        "mean_client_loss": 0.55,
                        "weighted_client_loss": 0.5,
                        "p90_client_loss": 0.8,
                        "worst_client_loss": 0.9,
                    },
                }
            ],
        }
        observations = runner._observations_from_summary(
            cell=self.cell(),
            summary=summary,
            commit_sha="commit-sha",
            specification_hash="spec-sha",
        )
        by_metric = {
            observation.metric: observation.value for observation in observations
        }
        self.assertAlmostEqual(by_metric["global_accuracy"], 0.8)
        self.assertAlmostEqual(by_metric["p10_client_accuracy"], 0.6)
        self.assertAlmostEqual(by_metric["worst_client_accuracy"], 0.5)
        self.assertAlmostEqual(by_metric["jain_accuracy_index"], 0.97)
        self.assertAlmostEqual(by_metric["worst_client_loss"], 0.9)

    def complete_summary(self) -> dict:
        return {
            "schema_version": 2,
            "dataset": "MNIST",
            "seed": 23,
            "partition": {"partition_hash": "train-sha"},
            "evaluation_partition": {
                "partition_hash": "test-sha",
                "parameters": {"source_training_partition_hash": "train-sha"},
            },
            "runs": [
                {
                    "algorithm": "fedavg",
                    "rounds_completed": 10,
                    "final_acc": 0.8,
                    "client_evaluation": {
                        "client_count": 2,
                        "mean_client_accuracy": 0.8,
                        "weighted_client_accuracy": 0.8,
                        "median_client_accuracy": 0.8,
                        "p10_client_accuracy": 0.7,
                        "worst_client_accuracy": 0.7,
                        "client_accuracy_std": 0.1,
                        "jain_accuracy_index": 0.98,
                    },
                }
            ],
        }

    def test_scientific_evidence_accepts_complete_heldout_metrics(self) -> None:
        runner._validate_completed_scientific_cell(self.cell(), self.complete_summary())

    def test_scientific_evidence_rejects_missing_client_metrics(self) -> None:
        summary = self.complete_summary()
        del summary["runs"][0]["client_evaluation"]["p10_client_accuracy"]
        with self.assertRaisesRegex(ValueError, "p10_client_accuracy"):
            runner._validate_completed_scientific_cell(self.cell(), summary)

    def test_scientific_evidence_rejects_mismatched_partition(self) -> None:
        summary = self.complete_summary()
        summary["evaluation_partition"]["parameters"][
            "source_training_partition_hash"
        ] = "different-train-sha"
        with self.assertRaisesRegex(ValueError, "provenance"):
            runner._validate_completed_scientific_cell(self.cell(), summary)

    def test_scientific_evidence_rejects_incomplete_rounds(self) -> None:
        summary = self.complete_summary()
        summary["runs"][0]["rounds_completed"] = 2
        with self.assertRaisesRegex(ValueError, "rounds"):
            runner._validate_completed_scientific_cell(self.cell(), summary)

    def test_scientific_evidence_rejects_global_client_disagreement(self) -> None:
        summary = self.complete_summary()
        summary["runs"][0]["final_acc"] = 0.9
        with self.assertRaisesRegex(ValueError, "disagrees"):
            runner._validate_completed_scientific_cell(self.cell(), summary)

    def test_resume_rejects_old_summary_without_completion_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "cells" / self.cell().cell_id / "results"
            results.mkdir(parents=True)
            (results / "summary.json").write_text(
                json.dumps(self.complete_summary()), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "completion receipt"):
                runner._run_cell(
                    cell=self.cell(),
                    base_config=self.base_config(),
                    benchmark_root=root,
                    resume=True,
                    dry_run=False,
                )

    def test_resume_uses_matching_receipt_without_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cell = self.cell()
            cell_root = root / "cells" / cell.cell_id
            results = cell_root / "results"
            results.mkdir(parents=True)
            summary = self.complete_summary()
            (results / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            config = runner._cell_config(self.base_config(), cell, results)
            runner._write_json(
                cell_root / "completion_receipt.json",
                {
                    "schema_version": 1,
                    "cell_id": cell.cell_id,
                    "request_hash": runner._config_hash(config),
                    "source_commit_sha": "test-commit",
                    "training_partition_hash": "train-sha",
                    "heldout_partition_hash": "test-sha",
                },
            )
            with mock.patch.object(runner, "_commit_sha", return_value="test-commit"):
                observed, status = runner._run_cell(
                    cell=cell,
                    base_config=self.base_config(),
                    benchmark_root=root,
                    resume=True,
                    dry_run=False,
                )
            self.assertEqual(status, "resumed")
            self.assertEqual(observed, summary)

            changed = self.base_config()
            changed["federated"]["sample_rate"] = 0.5
            with (
                mock.patch.object(runner, "_commit_sha", return_value="test-commit"),
                self.assertRaisesRegex(ValueError, "configuration changed"),
            ):
                runner._run_cell(
                    cell=cell,
                    base_config=changed,
                    benchmark_root=root,
                    resume=True,
                    dry_run=False,
                )

    def test_resume_rejects_missing_or_changed_source_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cell = self.cell()
            cell_root = root / "cells" / cell.cell_id
            results = cell_root / "results"
            results.mkdir(parents=True)
            (results / "summary.json").write_text(
                json.dumps(self.complete_summary()), encoding="utf-8"
            )
            config = runner._cell_config(self.base_config(), cell, results)
            for saved_sha in (None, "different-commit"):
                receipt = {
                    "schema_version": 1,
                    "cell_id": cell.cell_id,
                    "request_hash": runner._config_hash(config),
                    "training_partition_hash": "train-sha",
                    "heldout_partition_hash": "test-sha",
                }
                if saved_sha is not None:
                    receipt["source_commit_sha"] = saved_sha
                runner._write_json(cell_root / "completion_receipt.json", receipt)
                with (
                    self.subTest(saved_sha=saved_sha),
                    mock.patch.object(
                        runner, "_commit_sha", return_value="expected-commit"
                    ),
                    self.assertRaisesRegex(ValueError, "source commit"),
                ):
                    runner._run_cell(
                        cell=cell,
                        base_config=self.base_config(),
                        benchmark_root=root,
                        resume=True,
                        dry_run=False,
                    )


if __name__ == "__main__":
    unittest.main()
