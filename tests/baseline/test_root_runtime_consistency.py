from __future__ import annotations

import math
import unittest

import torch

from experiment_runtime import (
    _make_privacy_noise_generator,
    _sample_client_ids,
    build_summary_table,
    validate_config,
)
from federated.dp_accountant import MomentsAccountant, compose_rdp_curves, rdp_to_epsilon
from federated.server import Server
from models.networks import build_model


def config_template() -> dict:
    return {
        "system": {"seed": 42, "device": "cpu", "results_dir": "results"},
        "data": {
            "dataset": "MNIST",
            "data_root": "./data_raw",
            "partition": "dirichlet",
            "alpha": 0.1,
            "classes_per_client": 2,
            "min_partition_size": 2,
        },
        "federated": {
            "num_clients": 10,
            "sample_rate": 0.2,
            "sampling_strategy": "poisson",
            "aggregation_weighting": "uniform",
            "rounds": 3,
            "local_epochs": 1,
            "batch_size": 4,
            "server_lr": 1.0,
        },
        "optimizer": {
            "lr": 0.01,
            "momentum": 0.0,
            "weight_decay": 0.0,
            "grad_clip_norm": None,
        },
        "algorithm": {"name": "fedavg", "mu": 0.01},
        "dp": {
            "enabled": True,
            "update_clip_norm": 1.0,
            "noise_multiplier": 1.0,
            "target_delta": 1e-5,
            "deterministic_noise_for_testing": False,
            "test_noise_seed": None,
        },
        "model": {"name": "cnn", "group_norm_groups": 2},
        "evaluation": {"eval_batch_size": 8},
    }


class RootRuntimeValidationTests(unittest.TestCase):
    def test_poisson_sampling_q_zero_and_q_one(self) -> None:
        sampler = __import__("random").Random(7)
        self.assertEqual(_sample_client_ids(num_clients=5, sample_rate=0.0, strategy="poisson", sampler=sampler), [])
        selected = _sample_client_ids(num_clients=5, sample_rate=1.0, strategy="poisson", sampler=sampler)
        self.assertEqual(selected, [0, 1, 2, 3, 4])

    def test_dp_rejects_fixed_without_replacement(self) -> None:
        config = config_template()
        config["federated"]["sampling_strategy"] = "fixed_without_replacement"
        with self.assertRaisesRegex(ValueError, "Poisson"):
            validate_config(config)

    def test_dp_rejects_sample_count_weighting(self) -> None:
        config = config_template()
        config["federated"]["aggregation_weighting"] = "sample_count"
        with self.assertRaisesRegex(ValueError, "uniform client weighting"):
            validate_config(config)

    def test_scaffold_rejects_sample_count_weighting(self) -> None:
        config = config_template()
        config["dp"]["enabled"] = False
        config["algorithm"]["name"] = "scaffold"
        config["federated"]["aggregation_weighting"] = "sample_count"
        with self.assertRaisesRegex(ValueError, "SCAFFOLD"):
            validate_config(config)

    def test_legacy_max_grad_norm_is_migrated_with_warning(self) -> None:
        config = config_template()
        del config["dp"]["update_clip_norm"]
        config["dp"]["max_grad_norm"] = 1.7
        normalized, warnings = validate_config(config)
        self.assertEqual(normalized["dp"]["update_clip_norm"], 1.7)
        self.assertEqual(normalized["optimizer"]["grad_clip_norm"], 1.7)
        self.assertTrue(any("max_grad_norm" in warning for warning in warnings))

    def test_deterministic_noise_requires_seed(self) -> None:
        config = config_template()
        config["dp"]["test_noise_seed"] = None
        config["dp"]["deterministic_noise_for_testing"] = True
        with self.assertRaisesRegex(ValueError, "test_noise_seed"):
            validate_config(config)

    def test_noise_generator_marks_simulation_only(self) -> None:
        config = config_template()
        config["dp"]["deterministic_noise_for_testing"] = True
        config["dp"]["test_noise_seed"] = 99
        generator, status = _make_privacy_noise_generator(config["dp"])
        self.assertIsNotNone(generator)
        self.assertEqual(status, "simulation_only")


class RootRuntimeAggregationTests(unittest.TestCase):
    def test_empty_cohort_is_noop(self) -> None:
        model = build_model("cnn", num_classes=10, in_channels=1, group_norm_groups=2)
        server = Server(model=model, num_clients=5, algorithm="fedavg", aggregation_weighting="uniform")
        before = server.broadcast()
        stats = server.aggregate([])
        after = server.broadcast()
        self.assertEqual(stats["cohort_size"], 0)
        for name in before:
            self.assertTrue(torch.equal(before[name], after[name]))

    def test_uniform_aggregation_ignores_sample_counts(self) -> None:
        model = build_model("cnn", num_classes=10, in_channels=1, group_norm_groups=2)
        server = Server(model=model, num_clients=2, algorithm="fedavg", aggregation_weighting="uniform")
        before = server.broadcast()
        float_key = next(name for name, value in before.items() if torch.is_floating_point(value))
        server.aggregate(
            [
                {"client_id": 0, "num_samples": 100, "delta": {float_key: torch.ones_like(before[float_key])}},
                {"client_id": 1, "num_samples": 1, "delta": {float_key: 3 * torch.ones_like(before[float_key])}},
            ]
        )
        after = server.broadcast()
        expected = before[float_key] + 2 * torch.ones_like(before[float_key])
        self.assertTrue(torch.allclose(after[float_key], expected))

    def test_sample_count_weighting_matches_expected_average(self) -> None:
        model = build_model("cnn", num_classes=10, in_channels=1, group_norm_groups=2)
        server = Server(model=model, num_clients=2, algorithm="fedavg", aggregation_weighting="sample_count")
        before = server.broadcast()
        float_key = next(name for name, value in before.items() if torch.is_floating_point(value))
        server.aggregate(
            [
                {"client_id": 0, "num_samples": 3, "delta": {float_key: torch.ones_like(before[float_key])}},
                {"client_id": 1, "num_samples": 1, "delta": {float_key: torch.zeros_like(before[float_key])}},
            ]
        )
        after = server.broadcast()
        expected = before[float_key] + 0.75 * torch.ones_like(before[float_key])
        self.assertTrue(torch.allclose(after[float_key], expected))


class RootRuntimePrivacyAccountingTests(unittest.TestCase):
    def test_sigma_zero_reports_infinite_epsilon(self) -> None:
        accountant = MomentsAccountant(noise_multiplier=0.0, sample_rate=0.2, target_delta=1e-5)
        accountant.step()
        self.assertTrue(math.isinf(accountant.get_epsilon()))

    def test_q_zero_reports_zero_epsilon(self) -> None:
        accountant = MomentsAccountant(noise_multiplier=1.0, sample_rate=0.0, target_delta=1e-5)
        accountant.step(5)
        self.assertEqual(accountant.get_epsilon(), 0.0)

    def test_composed_rdp_not_smaller_than_individual(self) -> None:
        fedavg = MomentsAccountant(noise_multiplier=1.0, sample_rate=0.2, target_delta=1e-5)
        fedprox = MomentsAccountant(noise_multiplier=1.0, sample_rate=0.2, target_delta=1e-5)
        scaffold = MomentsAccountant(noise_multiplier=1.0, sample_rate=0.2, target_delta=1e-5)
        for accountant in (fedavg, fedprox, scaffold):
            accountant.step(2)
        composed = compose_rdp_curves([fedavg.get_total_rdp(), fedprox.get_total_rdp(), scaffold.get_total_rdp()])
        epsilon, _ = rdp_to_epsilon(orders=fedavg.orders, total_rdp=composed, delta=1e-5)
        self.assertGreaterEqual(epsilon, fedavg.get_epsilon())

    def test_summary_reports_composed_privacy(self) -> None:
        config = config_template()
        summaries = [
            {
                "algorithm": "fedavg",
                "final_acc": 0.8,
                "best_acc": 0.8,
                "final_loss": 1.0,
                "final_epsilon": 1.1,
                "mean_raw_drift": 0.1,
                "mean_clipped_drift": 0.05,
                "mean_weight_var": 0.01,
                "elapsed_sec": 2.0,
                "final_total_rdp": MomentsAccountant(noise_multiplier=1.0, sample_rate=0.2).get_total_rdp().tolist(),
                "rdp_orders": MomentsAccountant(noise_multiplier=1.0, sample_rate=0.2).orders,
                "privacy_status": "estimated",
            },
            {
                "algorithm": "fedprox",
                "final_acc": 0.7,
                "best_acc": 0.75,
                "final_loss": 1.2,
                "final_epsilon": 1.1,
                "mean_raw_drift": 0.2,
                "mean_clipped_drift": 0.1,
                "mean_weight_var": 0.02,
                "elapsed_sec": 3.0,
                "final_total_rdp": MomentsAccountant(noise_multiplier=1.0, sample_rate=0.2).get_total_rdp().tolist(),
                "rdp_orders": MomentsAccountant(noise_multiplier=1.0, sample_rate=0.2).orders,
                "privacy_status": "estimated",
            },
            {
                "algorithm": "scaffold",
                "final_acc": 0.6,
                "best_acc": 0.7,
                "final_loss": 1.3,
                "final_epsilon": 1.1,
                "mean_raw_drift": 0.3,
                "mean_clipped_drift": 0.2,
                "mean_weight_var": 0.03,
                "elapsed_sec": 4.0,
                "final_total_rdp": MomentsAccountant(noise_multiplier=1.0, sample_rate=0.2).get_total_rdp().tolist(),
                "rdp_orders": MomentsAccountant(noise_multiplier=1.0, sample_rate=0.2).orders,
                "privacy_status": "estimated",
            },
        ]
        for item in summaries:
            acc = MomentsAccountant(noise_multiplier=1.0, sample_rate=0.2)
            acc.step(2)
            item["final_total_rdp"] = acc.get_total_rdp().tolist()
            item["rdp_orders"] = acc.orders
        summary = build_summary_table(summaries, config)
        self.assertIn("Composed epsilon for all released outputs", summary)
        self.assertIn("Sampling strategy", summary)
        self.assertIn("Aggregation weighting", summary)
