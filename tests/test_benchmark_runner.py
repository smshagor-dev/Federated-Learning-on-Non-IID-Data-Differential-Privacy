from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from python.src.fl_platform.benchmark.matrix import BenchmarkCell

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_benchmark_matrix.py"
SPEC = importlib.util.spec_from_file_location("run_benchmark_matrix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


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

    def cell(self, *, epsilon: float | None = 4.0) -> BenchmarkCell:
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


if __name__ == "__main__":
    unittest.main()
