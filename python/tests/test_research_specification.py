from __future__ import annotations

import unittest

from fl_platform.datasets import DatasetRegistryEntry, FilesystemDatasetRegistry
from fl_platform.research import (
    AdaptiveClippingMode,
    DeterminismLevel,
    ExperimentSpecification,
    ExperimentSpecificationError,
    PartitionStrategy,
    PrivacyMode,
    SecureAggregationProvider,
    validate_experiment_specification,
)
from fl_platform.research.specification import (
    AdaptiveClippingConfiguration,
    AlgorithmConfiguration,
    DatasetConfiguration,
    ModelConfiguration,
    PartitionConfiguration,
    PrivacyConfiguration,
    RuntimeLimits,
    SecureAggregationConfiguration,
    SeedConfiguration,
)


def _base_specification() -> ExperimentSpecification:
    spec = ExperimentSpecification(
        schema_version=1,
        experiment_id="exp-research-001",
        experiment_name="FedAvg privacy comparison",
        research_question=(
            "How do privacy layers affect convergence on one fixed dataset?"
        ),
        dataset=DatasetConfiguration(
            dataset_id="cifar10",
            dataset_version="1.0",
            dataset_checksum="sha256:cifar10-demo",
            split_seed=7,
            train_split_fraction=0.8,
            validation_split_fraction=0.1,
            test_split_fraction=0.1,
        ),
        partition=PartitionConfiguration(
            strategy=PartitionStrategy.DIRICHLET,
            num_clients=5,
            seed=11,
            minimum_client_samples=4,
            alpha=0.3,
            partition_manifest_hash="manifest-hash-123",
        ),
        model=ModelConfiguration(
            model_id="groupnorm_cnn",
            model_version="v1",
            initialization_seed=19,
        ),
        algorithm=AlgorithmConfiguration(algorithm_id="fedavg"),
        privacy=PrivacyConfiguration(
            privacy_mode=PrivacyMode.USER_LEVEL,
            noise_multiplier=1.0,
            target_delta=1e-5,
            user_level_clip_norm=1.5,
            client_weighting="uniform",
        ),
        secure_aggregation=SecureAggregationConfiguration(
            provider=SecureAggregationProvider.SECAGG_NO_DROPOUT_EXPERIMENTAL,
            dropout_recovery_requested=False,
        ),
        adaptive_clipping=AdaptiveClippingConfiguration(
            mode=AdaptiveClippingMode.DISABLED
        ),
        runtime=RuntimeLimits(
            max_rounds=3,
            local_epochs=1,
            batch_size=8,
            learning_rate=0.01,
            evaluation_frequency=1,
            selected_clients_per_round=3,
        ),
        seeds=SeedConfiguration(
            seeds=[1, 2, 3],
            partition_seed=11,
            worker_assignment_seed=13,
            coordinator_seed=17,
        ),
        determinism_level=DeterminismLevel.STRICT_CPU,
    )
    spec.specification_hash = spec.compute_hash()
    return spec


class ResearchSpecificationTests(unittest.TestCase):
    def test_specification_hash_is_deterministic(self) -> None:
        spec_a = _base_specification()
        spec_b = _base_specification()
        self.assertEqual(spec_a.compute_hash(), spec_b.compute_hash())

    def test_valid_specification_is_accepted_and_hash_normalized(self) -> None:
        spec = _base_specification()
        validated = validate_experiment_specification(spec)
        self.assertEqual(validated.specification_hash, spec.compute_hash())

    def test_drop_out_recovery_request_is_rejected(self) -> None:
        spec = _base_specification()
        spec.secure_aggregation.dropout_recovery_requested = True
        with self.assertRaisesRegex(
            ExperimentSpecificationError, "dropout recovery remains blocked"
        ):
            validate_experiment_specification(spec)

    def test_adaptive_clipping_requires_user_level_or_hybrid(self) -> None:
        spec = _base_specification()
        spec.privacy.privacy_mode = PrivacyMode.NONE
        spec.adaptive_clipping = AdaptiveClippingConfiguration(
            mode=AdaptiveClippingMode.ENABLED,
            initial_bound=1.0,
            min_bound=0.5,
            max_bound=2.0,
            target_quantile=0.5,
            learning_rate=0.2,
            indicator_noise_multiplier=0.5,
        )
        with self.assertRaisesRegex(
            ExperimentSpecificationError,
            "adaptive clipping requires USER_LEVEL_DP or HYBRID",
        ):
            validate_experiment_specification(spec)

    def test_user_level_requires_uniform_weighting(self) -> None:
        spec = _base_specification()
        spec.privacy.client_weighting = "sample_count"
        with self.assertRaisesRegex(
            ExperimentSpecificationError, "uniform client weighting"
        ):
            validate_experiment_specification(spec)

    def test_combined_hybrid_epsilon_is_rejected(self) -> None:
        spec = _base_specification()
        spec.privacy.privacy_mode = PrivacyMode.HYBRID
        spec.privacy.combined_epsilon = 1.0
        with self.assertRaisesRegex(
            ExperimentSpecificationError, "combined_epsilon is forbidden"
        ):
            validate_experiment_specification(spec)

    def test_secure_aggregation_rejects_non_fedavg(self) -> None:
        spec = _base_specification()
        spec.algorithm.algorithm_id = "fedprox"
        with self.assertRaisesRegex(
            ExperimentSpecificationError, "supports only fedavg"
        ):
            validate_experiment_specification(spec)


class ResearchPartitionTests(unittest.TestCase):
    def test_quantity_skew_partition_is_reproducible_and_has_metrics(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            registry = FilesystemDatasetRegistry(tmp)
            registry.register(
                DatasetRegistryEntry(
                    dataset_id="synthetic_registry_ds",
                    name="Synthetic Registry Dataset",
                    version="v1",
                    task_type="classification",
                    num_classes=4,
                    input_shape=[3, 32, 32],
                    train_sample_count=120,
                    eval_sample_count=20,
                    normalization="none",
                    storage_reference="synthetic://registry",
                    checksum="sha256:synthetic-registry",
                )
            )
            registry.validate("synthetic_registry_ds")
            registry.activate("synthetic_registry_ds")

            part_a = registry.create_partition(
                "synthetic_registry_ds",
                "quantity-a",
                "quantity_skew",
                seed=23,
                num_clients=5,
                quantity_skew_sigma=0.8,
                minimum_client_samples=3,
            )
            part_b = registry.create_partition(
                "synthetic_registry_ds",
                "quantity-b",
                "quantity_skew",
                seed=23,
                num_clients=5,
                quantity_skew_sigma=0.8,
                minimum_client_samples=3,
            )

            self.assertEqual(part_a.client_sample_counts, part_b.client_sample_counts)
            self.assertEqual(part_a.manifest_checksum, part_b.manifest_checksum)
            self.assertEqual(part_a.dataset_version, "v1")
            self.assertEqual(part_a.dataset_checksum, "sha256:synthetic-registry")
            self.assertIn("quantity_skew_coefficient", part_a.heterogeneity_metrics)
            self.assertGreaterEqual(
                part_a.heterogeneity_metrics["quantity_skew_coefficient"], 0.0
            )


if __name__ == "__main__":
    unittest.main()
