"""Algorithm Expansion phase foundation tests: real FedSAM/Ditto/Per-FedAvg training,
real model/dataset registries, and personalization/fairness metrics.

Supersedes the Foundation-era placeholder that tested pure scalar-vector
math (build_fedsam_step/compute_ditto_regularized_weights/
build_per_fedavg_step) — those functions were explicitly documented as
"not yet wired into the legacy trainer," and this phase's job was to
complete that wiring with real PyTorch training. See
docs/algorithm-expansion-architecture.md.
"""

import tempfile
import unittest

import torch

from fl_platform.algorithms import (
    LocalEvaluationContext,
    LocalTrainingContext,
    get_algorithm,
)
from fl_platform.datasets import DatasetRegistryEntry, FilesystemDatasetRegistry
from fl_platform.models import (
    PERSONALIZED_PREFIXES,
    SHARED_PREFIXES,
    FilesystemModelRegistry,
    ModelRegistryEntry,
    ModelRegistryError,
    build_model,
    describe_model,
)
from fl_platform.personalization import (
    PerClientEvaluationRecord,
    compute_aggregated_personalization_metrics,
    summarize_personalization,
)
from fl_platform.worker.dataset_loader import PartitionManifest
from fl_platform.worker.task_runner import build_bridge_compatible_model


def _make_context(
    algorithm: str, algorithm_config: dict, model: torch.nn.Module, seed: int = 1
) -> LocalTrainingContext:
    partition = PartitionManifest(
        dataset_id="synthetic",
        partition_id="p1",
        client_id="c1",
        sample_count=32,
        seed=seed,
        num_classes=3,
        in_channels=1,
        image_size=4,
    )
    return LocalTrainingContext(
        run_id="r1",
        round_id=1,
        client_id="c1",
        task_id="t1",
        algorithm=algorithm,
        model_version="v0",
        global_model=model,
        dataset_partition=partition,
        device=torch.device("cpu"),
        seed=seed,
        algorithm_config=algorithm_config,
        optimizer_config={},
        evaluation_config={},
    )


class FedSamTests(unittest.TestCase):
    def test_two_pass_training_produces_finite_update_and_matches_rho(self) -> None:
        model = build_bridge_compatible_model(
            num_classes=3, in_channels=1, image_size=4
        )
        algo = get_algorithm("fedsam")
        context = _make_context(
            "fedsam",
            {"rho": 0.05, "local_epochs": 1, "learning_rate": 0.05, "batch_size": 8},
            model,
        )
        algo.validate_task(context)
        result = algo.train(context)

        self.assertFalse(result.is_non_finite)
        self.assertGreater(result.sample_count, 0)
        # The perturbation is rho-scaled by construction (see fedsam.py);
        # its measured norm must equal rho, not just be "some" value.
        self.assertAlmostEqual(
            result.algorithm_metrics["perturbation_norm"], 0.05, places=5
        )
        self.assertIn("sharpness_proxy", result.algorithm_metrics)
        for tensor in result.global_update.values():
            self.assertTrue(torch.isfinite(tensor).all())

    def test_rejects_non_positive_rho(self) -> None:
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        algo = get_algorithm("fedsam")
        context = _make_context("fedsam", {"rho": 0.0}, model)
        with self.assertRaises(ValueError):
            algo.validate_task(context)

    def test_parameters_are_restored_after_second_pass(self) -> None:
        # Not just "the delta is finite" — the *model object* itself must
        # not be left perturbed after train() returns (the try/finally
        # restoration path).
        model = build_bridge_compatible_model(
            num_classes=3, in_channels=1, image_size=4
        )
        before = {name: p.detach().clone() for name, p in model.named_parameters()}
        algo = get_algorithm("fedsam")
        context = _make_context(
            "fedsam",
            {"rho": 0.05, "local_epochs": 1, "learning_rate": 0.0, "batch_size": 8},
            model,
        )
        algo.train(context)
        # learning_rate=0.0 means the optimizer step is a no-op, so any
        # residual difference from `before` can only be leftover
        # perturbation that wasn't restored.
        for name, param in model.named_parameters():
            self.assertTrue(
                torch.allclose(param.detach(), before[name], atol=1e-6), name
            )


class DittoTests(unittest.TestCase):
    def test_trains_global_and_personalized_models(self) -> None:
        model = build_bridge_compatible_model(
            num_classes=3, in_channels=1, image_size=4
        )
        algo = get_algorithm("ditto")
        context = _make_context(
            "ditto",
            {
                "regularization_coefficient": 0.5,
                "global_local_epochs": 1,
                "personalized_local_epochs": 1,
                "global_learning_rate": 0.05,
                "personalized_learning_rate": 0.05,
                "batch_size": 8,
            },
            model,
        )
        algo.validate_task(context)
        result = algo.train(context)

        self.assertIsNotNone(result.personalized_checkpoint)
        self.assertFalse(result.is_non_finite)
        self.assertIn("regularization_loss", result.algorithm_metrics)
        # Cold start: global_reference == personalized starting point, so
        # the regularization loss after one step should be small but the
        # checkpoint must differ from a pure copy of the global model
        # (personalized training actually happened).
        global_params = dict(model.named_parameters())
        for name, tensor in result.personalized_checkpoint.items():
            self.assertFalse(torch.equal(tensor, global_params[name].detach()))

    def test_warm_start_reuses_previous_personalized_checkpoint(self) -> None:
        model = build_bridge_compatible_model(
            num_classes=3, in_channels=1, image_size=4
        )
        algo = get_algorithm("ditto")
        config = {
            "regularization_coefficient": 0.5,
            "global_local_epochs": 1,
            "personalized_local_epochs": 1,
            "global_learning_rate": 0.05,
            "personalized_learning_rate": 0.05,
            "batch_size": 8,
            "warm_start_policy": "warm",
        }
        context = _make_context("ditto", config, model)
        first_result = algo.train(context)

        personalized_model = build_bridge_compatible_model(
            num_classes=3, in_channels=1, image_size=4
        )
        personalized_model.load_state_dict(first_result.personalized_checkpoint)
        context2 = _make_context("ditto", config, model, seed=2)
        context2.personalized_model = personalized_model
        second_result = algo.train(context2)

        self.assertIsNotNone(second_result.personalized_checkpoint)

    def test_rejects_non_positive_regularization(self) -> None:
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        algo = get_algorithm("ditto")
        context = _make_context("ditto", {"regularization_coefficient": 0.0}, model)
        with self.assertRaises(ValueError):
            algo.validate_task(context)


class PerFedAvgTests(unittest.TestCase):
    def test_support_query_split_is_deterministic(self) -> None:
        algo = get_algorithm("per_fedavg")
        config = {
            "inner_learning_rate": 0.05,
            "outer_learning_rate": 0.05,
            "inner_steps": 2,
            "meta_steps": 1,
            "minimum_samples_required": 4,
        }
        # torch.manual_seed before each model construction: nn.init draws
        # from the *global* RNG, so two separately-constructed models
        # only start from identical weights if that global state is reset
        # first — this is a test-determinism concern, not something
        # per_fedavg.py itself needs to account for (its own
        # support/query split uses its own seeded generator already).
        torch.manual_seed(123)
        model1 = build_bridge_compatible_model(
            num_classes=3, in_channels=1, image_size=4
        )
        torch.manual_seed(123)
        model2 = build_bridge_compatible_model(
            num_classes=3, in_channels=1, image_size=4
        )

        context1 = _make_context("per_fedavg", config, model1, seed=7)
        context2 = _make_context("per_fedavg", config, model2, seed=7)
        result1 = algo.train(context1)
        result2 = algo.train(context2)
        self.assertEqual(
            result1.algorithm_metrics["support_loss"],
            result2.algorithm_metrics["support_loss"],
        )

    def test_meta_update_produces_finite_global_update(self) -> None:
        model = build_bridge_compatible_model(
            num_classes=3, in_channels=1, image_size=4
        )
        algo = get_algorithm("per_fedavg")
        context = _make_context(
            "per_fedavg",
            {
                "inner_learning_rate": 0.05,
                "outer_learning_rate": 0.05,
                "inner_steps": 2,
                "meta_steps": 2,
                "minimum_samples_required": 4,
            },
            model,
        )
        algo.validate_task(context)
        result = algo.train(context)
        self.assertFalse(result.is_non_finite)
        self.assertEqual(result.algorithm_metrics["skipped_client"], 0.0)
        for tensor in result.global_update.values():
            self.assertTrue(torch.isfinite(tensor).all())

    def test_small_client_fallback_skips_rather_than_crashing(self) -> None:
        model = build_bridge_compatible_model(
            num_classes=3, in_channels=1, image_size=4
        )
        algo = get_algorithm("per_fedavg")
        partition = PartitionManifest(
            dataset_id="synthetic",
            partition_id="p1",
            client_id="tiny",
            sample_count=1,
            seed=1,
            num_classes=3,
            in_channels=1,
            image_size=4,
        )
        context = LocalTrainingContext(
            run_id="r1",
            round_id=1,
            client_id="tiny",
            task_id="t1",
            algorithm="per_fedavg",
            model_version="v0",
            global_model=model,
            dataset_partition=partition,
            device=torch.device("cpu"),
            seed=1,
            algorithm_config={
                "minimum_samples_required": 4,
                "fallback_behavior": "skip",
            },
            optimizer_config={},
            evaluation_config={},
        )
        result = algo.train(context)
        self.assertEqual(result.algorithm_metrics["skipped_client"], 1.0)
        self.assertEqual(result.sample_count, 0)

    def test_post_adaptation_evaluation_runs(self) -> None:
        model = build_bridge_compatible_model(
            num_classes=3, in_channels=1, image_size=4
        )
        algo = get_algorithm("per_fedavg")
        partition = PartitionManifest(
            dataset_id="synthetic",
            partition_id="p1",
            client_id="c1",
            sample_count=32,
            seed=1,
            num_classes=3,
            in_channels=1,
            image_size=4,
        )
        context = LocalEvaluationContext(
            run_id="r1",
            round_id=1,
            client_id="c1",
            algorithm="per_fedavg",
            global_model=model,
            dataset_partition=partition,
            device=torch.device("cpu"),
            evaluation_config={
                "adaptation_steps_eval": 2,
                "inner_learning_rate": 0.05,
                "batch_size": 8,
            },
        )
        result = algo.evaluate(context)
        self.assertIsNotNone(result.personalized_model_local_accuracy)


class ModelRegistryTests(unittest.TestCase):
    def test_lifecycle_and_resolve_for_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = FilesystemModelRegistry(tmp)
            model = build_model(
                "personalizable_bridge", num_classes=3, in_channels=1, image_size=4
            )
            meta = describe_model(
                model,
                "personalizable_bridge",
                "v1",
                input_channels=1,
                num_classes=3,
                normalization_type="none",
                shared_parameter_prefixes=SHARED_PREFIXES["personalizable_bridge"],
                personalized_parameter_prefixes=PERSONALIZED_PREFIXES[
                    "personalizable_bridge"
                ],
            )
            entry = ModelRegistryEntry(
                name="personalizable_bridge",
                version="v1",
                architecture_name="personalizable_bridge",
                input_channels=1,
                num_classes=3,
                normalization="none",
                parameter_count=meta.parameter_count,
                state_dict_schema_hash=meta.state_dict_schema_hash,
                aggregatable_parameter_names=meta.shared_parameter_names,
                personalizable_parameter_names=meta.personalized_parameter_names,
                supported_algorithms=["ditto", "fedavg"],
            )
            registry.register(entry)
            registry.validate(
                "personalizable_bridge",
                "v1",
                actual_schema_hash=meta.state_dict_schema_hash,
            )
            registry.activate("personalizable_bridge", "v1")
            resolved = registry.resolve_for_task("personalizable_bridge", "ditto")
            self.assertEqual(resolved.status, "ACTIVE")
            with self.assertRaises(ModelRegistryError):
                registry.resolve_for_task("personalizable_bridge", "scaffold")


class DatasetRegistryTests(unittest.TestCase):
    def test_partition_strategies_are_reproducible_and_respect_constraints(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = FilesystemDatasetRegistry(tmp)
            registry.register(
                DatasetRegistryEntry(
                    dataset_id="synthetic_cifar_like",
                    name="Synthetic",
                    version="v1",
                    task_type="classification",
                    num_classes=4,
                    input_shape=[3, 32, 32],
                    train_sample_count=200,
                    eval_sample_count=50,
                    normalization="none",
                    storage_reference="synthetic://in-memory",
                )
            )
            registry.validate("synthetic_cifar_like")
            registry.activate("synthetic_cifar_like")

            first = registry.create_partition(
                "synthetic_cifar_like",
                "part-a",
                "dirichlet",
                seed=1,
                num_clients=5,
                alpha=0.3,
            )
            second = registry.create_partition(
                "synthetic_cifar_like",
                "part-b",
                "dirichlet",
                seed=1,
                num_clients=5,
                alpha=0.3,
            )
            self.assertEqual(first.client_sample_counts, second.client_sample_counts)
            self.assertEqual(first.manifest_checksum, second.manifest_checksum)

            pathological = registry.create_partition(
                "synthetic_cifar_like",
                "part-patho",
                "pathological",
                seed=1,
                num_clients=5,
                classes_per_client=2,
            )
            for summary in pathological.label_distribution_summary.values():
                self.assertLessEqual(len(summary), 2)

    def test_dataset_registry_defaults_still_available(self) -> None:
        from fl_platform.datasets import DatasetRegistry

        registry = DatasetRegistry.with_default_registry()
        self.assertIn("cifar10", registry.list_names())
        self.assertIn(
            "quantity_skew",
            registry.get("custom_manifest_dataset").supports_partitioning,
        )


class PersonalizationMetricsTests(unittest.TestCase):
    def test_personalization_summary(self) -> None:
        summary = summarize_personalization(
            global_accuracy=0.60,
            personalized_accuracies=[0.55, 0.65, 0.75, 0.80, 0.70],
        )
        self.assertAlmostEqual(summary.mean_personalized_accuracy, 0.69)
        self.assertAlmostEqual(summary.median_personalized_accuracy, 0.70)
        self.assertAlmostEqual(summary.worst_client_accuracy, 0.55)
        self.assertAlmostEqual(summary.fairness_gap, 0.25)

    def test_aggregated_metrics_handle_excluded_clients(self) -> None:
        records = [
            PerClientEvaluationRecord("c1", 0.5, 0.6, 10),
            PerClientEvaluationRecord("c2", 0.5, None, 10),
            PerClientEvaluationRecord("c3", 0.5, 0.7, 0),
        ]
        metrics = compute_aggregated_personalization_metrics(records)
        self.assertEqual(metrics.client_count, 1)
        self.assertEqual(metrics.excluded_client_count, 2)


if __name__ == "__main__":
    unittest.main()
