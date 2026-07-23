import unittest

from fl_platform.algorithms import (
    DittoConfig,
    DittoState,
    FedSAMConfig,
    PerFedAvgConfig,
    build_fedsam_step,
    build_per_fedavg_step,
    compute_ditto_regularized_weights,
)
from fl_platform.datasets import DatasetRegistry
from fl_platform.models import ModelRegistry
from fl_platform.personalization import summarize_personalization


class Milestone4FoundationTests(unittest.TestCase):
    def test_fedsam_step_is_deterministic(self) -> None:
        step = build_fedsam_step(
            weights=[1.0, -2.0],
            gradients=[3.0, 4.0],
            config=FedSAMConfig(rho=0.5, adaptive=False),
        )
        self.assertAlmostEqual(step.gradient_norm, 5.0)
        self.assertAlmostEqual(step.scale, 0.1)
        self.assertEqual(step.perturbation, [0.30000000000000004, 0.4])

    def test_ditto_regularized_weights(self) -> None:
        updated = compute_ditto_regularized_weights(
            DittoState(global_weights=[1.0, 1.0], local_weights=[2.0, 0.0]),
            gradients=[0.2, -0.2],
            config=DittoConfig(regularization=0.5, personalized_learning_rate=0.1),
        )
        self.assertAlmostEqual(updated[0], 1.93)
        self.assertAlmostEqual(updated[1], 0.07)

    def test_per_fedavg_step(self) -> None:
        step = build_per_fedavg_step(
            weights=[1.0, 2.0],
            support_gradients=[0.5, 0.25],
            query_gradients=[0.2, 0.1],
            config=PerFedAvgConfig(inner_lr=0.1, meta_lr=0.2, first_order=True),
        )
        self.assertEqual(step.adapted_weights, [0.95, 1.975])
        self.assertEqual(step.meta_weights, [0.9099999999999999, 1.955])

    def test_model_registry_defaults(self) -> None:
        registry = ModelRegistry.with_milestone_defaults()
        self.assertIn("groupnorm_cnn", registry.list_names())
        self.assertEqual(registry.get("vit_tiny").normalization, "layernorm")

    def test_dataset_registry_defaults(self) -> None:
        registry = DatasetRegistry.with_milestone_defaults()
        self.assertIn("cifar10", registry.list_names())
        self.assertIn("quantity_skew", registry.get("custom_manifest_dataset").supports_partitioning)

    def test_personalization_summary(self) -> None:
        summary = summarize_personalization(
            global_accuracy=0.60,
            personalized_accuracies=[0.55, 0.65, 0.75, 0.80, 0.70],
        )
        self.assertAlmostEqual(summary.mean_personalized_accuracy, 0.69)
        self.assertAlmostEqual(summary.median_personalized_accuracy, 0.70)
        self.assertAlmostEqual(summary.worst_client_accuracy, 0.55)
        self.assertAlmostEqual(summary.fairness_gap, 0.25)


if __name__ == "__main__":
    unittest.main()
