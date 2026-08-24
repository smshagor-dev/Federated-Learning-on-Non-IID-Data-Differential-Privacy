from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_local_execution.py"
SPEC = importlib.util.spec_from_file_location("run_local_execution", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def canonical_spec() -> dict:
    return {
        "schema_version": 1,
        "name": "local-mapping",
        "backend": "local",
        "dataset": {
            "name": "MNIST",
            "reference": "",
            "partition": {
                "strategy": "iid",
                "minimum_client_size": 10,
            },
        },
        "model": {
            "name": "root-cnn",
            "version": "v1",
            "architecture_name": "cnn",
            "update_format": "state_dict_delta",
            "tensors": [{"name": "weight", "shape": [2, 2]}],
            "aggregation": {"shared_parameter_names": ["weight"]},
        },
        "algorithm": {"name": "fedavg", "mu": 0.0},
        "optimizer": {
            "learning_rate": 0.01,
            "momentum": 0.9,
            "weight_decay": 0.0005,
            "server_lr": 1.0,
        },
        "federation": {
            "total_clients": 20,
            "client_ids": [],
            "target_clients_per_round": 5,
            "minimum_valid_results": 3,
            "rounds": 10,
            "local_epochs": 2,
            "batch_size": 64,
            "weighting": "uniform",
            "sampling_strategy": "fixed_without_replacement",
            "client_selection_seed": 17,
            "scheduling_mode": "synchronous",
            "round_timeout_seconds": 60,
            "task_lease_seconds": 30,
            "max_task_retries": 2,
        },
        "privacy": {
            "mode": "none",
            "sample_level": {},
            "user_level": {},
            "adaptive_clipping": {"enabled": False},
            "warning_threshold_fraction": 0.8,
        },
        "evaluation": {
            "evaluate_global": True,
            "evaluate_per_client": True,
            "evaluate_fairness": True,
            "evaluation_batch_size": 128,
        },
        "artifacts": {
            "root": "artifacts/canonical-local-test",
            "persist_checkpoints": True,
            "persist_round_metrics": True,
            "persist_client_metrics": True,
            "persist_events": True,
        },
        "security": {
            "require_authenticated_workers": False,
            "require_signed_tasks": False,
            "require_signed_results": False,
            "secure_aggregation": False,
        },
    }


def private_spec(*, accountant: str = "rdp", epsilon_budget: float = 0.0) -> dict:
    spec = canonical_spec()
    spec["federation"]["sampling_strategy"] = "poisson"
    spec["algorithm"] = {"name": "fedprox", "mu": 0.02}
    spec["privacy"] = {
        "mode": "user_level_dp",
        "sample_level": {},
        "user_level": {
            "noise_multiplier": 2.0,
            "target_delta": 1e-5,
            "accountant": accountant,
            "initial_clipping_bound": 1.5,
            "weighting_strategy": "uniform",
            "secure_random": False,
            "epsilon_budget": epsilon_budget,
        },
        "adaptive_clipping": {"enabled": False},
        "warning_threshold_fraction": 0.8,
    }
    return spec


class CanonicalLocalExecutionTests(unittest.TestCase):
    def test_fixed_sampling_maps_exact_expected_root_cohort(self) -> None:
        config = adapter.build_root_config(canonical_spec())
        self.assertEqual(config["data"]["dataset"], "MNIST")
        self.assertEqual(config["data"]["partition"], "iid")
        self.assertEqual(config["algorithm"]["name"], "fedavg")
        self.assertEqual(
            config["federated"]["sampling_strategy"],
            "fixed_without_replacement",
        )
        self.assertAlmostEqual(config["federated"]["sample_rate"], 0.25)
        self.assertFalse(config["dp"]["enabled"])
        self.assertEqual(config["system"]["seed"], 17)

    def test_user_level_dp_maps_to_existing_root_privacy_path(self) -> None:
        config = adapter.build_root_config(private_spec())
        self.assertTrue(config["dp"]["enabled"])
        self.assertIsNone(config["dp"]["target_epsilon"])
        self.assertEqual(config["dp"]["target_delta"], 1e-5)
        self.assertEqual(config["dp"]["update_clip_norm"], 1.5)
        self.assertEqual(config["dp"]["noise_multiplier"], 2.0)
        self.assertEqual(config["algorithm"]["name"], "fedprox")
        self.assertEqual(config["algorithm"]["mu"], 0.02)

    def test_non_rdp_accountant_is_rejected_instead_of_silently_changed(self) -> None:
        with self.assertRaisesRegex(ValueError, "RDP accountant"):
            adapter.build_root_config(private_spec(accountant="prv"))

    def test_epsilon_budget_is_not_reinterpreted_as_target_calibration(self) -> None:
        with self.assertRaisesRegex(ValueError, "stop-policy enforcement"):
            adapter.build_root_config(private_spec(epsilon_budget=4.0))

    def test_sample_level_and_hybrid_privacy_are_rejected(self) -> None:
        for mode in ("sample_level_dp", "hybrid_dp"):
            with self.subTest(mode=mode):
                spec = canonical_spec()
                spec["privacy"]["mode"] = mode
                with self.assertRaisesRegex(ValueError, "supports privacy.mode"):
                    adapter.build_root_config(spec)

    def test_local_security_claims_are_rejected(self) -> None:
        for field in (
            "require_authenticated_workers",
            "require_signed_tasks",
            "require_signed_results",
            "secure_aggregation",
        ):
            with self.subTest(field=field):
                spec = canonical_spec()
                spec["security"][field] = True
                with self.assertRaisesRegex(ValueError, "distributed backend"):
                    adapter.build_root_config(spec)

    def test_local_pause_style_scheduling_is_rejected(self) -> None:
        spec = copy.deepcopy(canonical_spec())
        spec["federation"]["scheduling_mode"] = "deadline_based_semi_synchronous"
        with self.assertRaisesRegex(ValueError, "synchronous scheduling only"):
            adapter.build_root_config(spec)


if __name__ == "__main__":
    unittest.main()
