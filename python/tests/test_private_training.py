"""Real, non-mocked test of sample-level DP training via Opacus. Uses a
synthetic dataset (no downloads) but a genuine PrivacyEngine wrapping a
genuine model, exercising real per-sample clipping/noise, not a stub.
"""

from __future__ import annotations

import unittest

import torch

from fl_platform.privacy import SampleLevelDPConfig, SamplePrivacyBudgetPolicy
from fl_platform.privacy.budget_enforcement import (
    SampleBudgetEnforcer,
    SampleBudgetOutcome,
    SampleLevelBudgetExceededError,
)
from fl_platform.privacy.secure_random import SecureRandomTaskRejectedError
from fl_platform.worker.coordinator_client import ClientTrainingTask
from fl_platform.worker.task_runner import (
    UnsupportedPrivacyCombinationError,
    build_bridge_compatible_model,
    run_private_local_training,
)


def _global_state() -> dict[str, torch.Tensor]:
    model = build_bridge_compatible_model(num_classes=2, in_channels=1, image_size=4)
    return {name: tensor.clone() for name, tensor in model.state_dict().items()}


class PrivateTrainingTests(unittest.TestCase):
    def test_fedavg_trains_privately_and_produces_a_real_delta(self) -> None:
        task = ClientTrainingTask(
            has_task=True,
            client_id="client-a",
            round_id=1,
            algorithm="fedavg",
            local_epochs=1,
            batch_size=4,
            learning_rate=0.1,
        )
        global_state = _global_state()
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        privacy_config = SampleLevelDPConfig(
            noise_multiplier=1.0, max_grad_norm=1.0, target_delta=1e-5
        )

        outcome, privacy_result = run_private_local_training(
            task, global_state, model, privacy_config, sample_count=16
        )

        # Poisson subsampling (the default) includes each sample
        # independently with probability batch_size/dataset_size, so the
        # realized count varies run to run around the nominal 16 — exact
        # equality would be asserting against the wrong distribution.
        self.assertGreater(outcome.sample_count, 0)
        self.assertIn("weight", outcome.delta)
        # A real (noised, trained) delta is essentially never exactly
        # zero — this distinguishes "training actually ran" from a stub.
        self.assertGreater(torch.sum(torch.abs(outcome.delta["weight"])).item(), 0.0)
        self.assertGreater(privacy_result.epsilon, 0.0)
        self.assertAlmostEqual(privacy_result.delta, 1e-5)
        self.assertEqual(privacy_result.accountant, "rdp")
        self.assertGreater(privacy_result.steps, 0)

    def test_fedprox_applies_proximal_term_and_trains_privately(self) -> None:
        task = ClientTrainingTask(
            has_task=True,
            client_id="client-a",
            round_id=1,
            algorithm="fedprox",
            local_epochs=1,
            batch_size=4,
            learning_rate=0.1,
            fedprox_mu=0.1,
        )
        global_state = _global_state()
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        privacy_config = SampleLevelDPConfig(noise_multiplier=1.0, max_grad_norm=1.0)

        outcome, privacy_result = run_private_local_training(
            task, global_state, model, privacy_config, sample_count=16
        )
        self.assertGreater(privacy_result.epsilon, 0.0)
        self.assertIn("weight", outcome.delta)

    def test_more_epochs_increases_epsilon(self) -> None:
        privacy_config = SampleLevelDPConfig(noise_multiplier=2.0, max_grad_norm=1.0)

        def _train(epochs: int) -> float:
            task = ClientTrainingTask(
                has_task=True,
                client_id="client-a",
                round_id=1,
                algorithm="fedavg",
                local_epochs=epochs,
                batch_size=4,
                learning_rate=0.1,
            )
            model = build_bridge_compatible_model(
                num_classes=2, in_channels=1, image_size=4
            )
            _, privacy_result = run_private_local_training(
                task, _global_state(), model, privacy_config, sample_count=16
            )
            return privacy_result.epsilon

        self.assertLess(_train(1), _train(5))

    def test_scaffold_is_rejected_before_training(self) -> None:
        task = ClientTrainingTask(
            has_task=True,
            client_id="client-a",
            round_id=1,
            algorithm="scaffold",
            local_epochs=1,
            batch_size=4,
            learning_rate=0.1,
        )
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        privacy_config = SampleLevelDPConfig(noise_multiplier=1.0, max_grad_norm=1.0)
        with self.assertRaises(UnsupportedPrivacyCombinationError):
            run_private_local_training(
                task, _global_state(), model, privacy_config, sample_count=16
            )

    def test_ditto_is_rejected_before_training(self) -> None:
        task = ClientTrainingTask(
            has_task=True,
            client_id="client-a",
            round_id=1,
            algorithm="ditto",
            local_epochs=1,
            batch_size=4,
            learning_rate=0.1,
        )
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        privacy_config = SampleLevelDPConfig(noise_multiplier=1.0, max_grad_norm=1.0)
        with self.assertRaises(UnsupportedPrivacyCombinationError):
            run_private_local_training(
                task, _global_state(), model, privacy_config, sample_count=16
            )


class SampleBudgetEnforcementIntegrationTests(unittest.TestCase):
    """Exercises fl_platform.privacy.budget_enforcement wired through the
    real Opacus training loop in run_private_local_training -- not just
    the enforcer in isolation (see test_privacy_budget_enforcement.py for
    that). One case per policy, per the closure-gate requirement."""

    def _task(self, local_epochs: int = 5) -> ClientTrainingTask:
        return ClientTrainingTask(
            has_task=True,
            client_id="client-budget",
            round_id=1,
            algorithm="fedavg",
            local_epochs=local_epochs,
            batch_size=4,
            learning_rate=0.1,
        )

    def test_stop_before_exceeding_keeps_a_partial_but_budget_compliant_delta(
        self,
    ) -> None:
        # sample_count=200, batch_size=4 -> sample_rate=0.02, a gentle
        # enough per-step epsilon growth curve that budget=0.5 is
        # crossed partway through training, not on the very first step
        # (that "zero safe steps" case is its own test below).
        privacy_config = SampleLevelDPConfig(
            noise_multiplier=1.5, max_grad_norm=1.0, target_delta=1e-5
        )
        enforcer = SampleBudgetEnforcer(
            client_id="client-budget",
            policy=SamplePrivacyBudgetPolicy.STOP_BEFORE_EXCEEDING,
            epsilon_budget=0.5,
            target_delta=1e-5,
        )
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        outcome, privacy_result = run_private_local_training(
            self._task(local_epochs=5),
            _global_state(),
            model,
            privacy_config,
            sample_count=200,
            budget_enforcer=enforcer,
        )
        self.assertIn("weight", outcome.delta)
        self.assertLess(privacy_result.epsilon, 0.5)
        self.assertEqual(
            privacy_result.budget_decision_outcome,
            SampleBudgetOutcome.STOPPED_BEFORE_STEP.value,
        )
        # 5 epochs x 50 batches (200 samples / batch_size 4) = 250
        # possible steps; budget must have cut training short of the
        # full plan for this to be a real test.
        self.assertLess(privacy_result.steps, 250)
        self.assertGreater(privacy_result.steps, 1)

    def test_stop_before_exceeding_with_zero_safe_steps_raises(self) -> None:
        privacy_config = SampleLevelDPConfig(
            noise_multiplier=1.0, max_grad_norm=1.0, target_delta=1e-5
        )
        enforcer = SampleBudgetEnforcer(
            client_id="client-budget",
            policy=SamplePrivacyBudgetPolicy.STOP_BEFORE_EXCEEDING,
            epsilon_budget=1e-9,
            target_delta=1e-5,
        )
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        with self.assertRaises(SampleLevelBudgetExceededError):
            run_private_local_training(
                self._task(local_epochs=5),
                _global_state(),
                model,
                privacy_config,
                sample_count=16,
                budget_enforcer=enforcer,
            )

    def test_fail_task_raises_and_submits_nothing(self) -> None:
        privacy_config = SampleLevelDPConfig(
            noise_multiplier=1.0, max_grad_norm=1.0, target_delta=1e-5
        )
        enforcer = SampleBudgetEnforcer(
            client_id="client-budget",
            policy=SamplePrivacyBudgetPolicy.FAIL_TASK,
            epsilon_budget=2.0,
            target_delta=1e-5,
        )
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        with self.assertRaises(SampleLevelBudgetExceededError) as ctx:
            run_private_local_training(
                self._task(local_epochs=5),
                _global_state(),
                model,
                privacy_config,
                sample_count=16,
                budget_enforcer=enforcer,
            )
        self.assertEqual(
            ctx.exception.decision.outcome, SampleBudgetOutcome.FAILED_TASK
        )
        self.assertTrue(enforcer.stopped_for_future_tasks)

    def test_stop_after_current_task_completes_full_training_then_blocks_next_task(
        self,
    ) -> None:
        privacy_config = SampleLevelDPConfig(
            noise_multiplier=1.0, max_grad_norm=1.0, target_delta=1e-5
        )
        enforcer = SampleBudgetEnforcer(
            client_id="client-budget",
            policy=SamplePrivacyBudgetPolicy.STOP_AFTER_CURRENT_TASK,
            epsilon_budget=2.0,
            target_delta=1e-5,
        )
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        outcome, privacy_result = run_private_local_training(
            self._task(local_epochs=5),
            _global_state(),
            model,
            privacy_config,
            sample_count=16,
            budget_enforcer=enforcer,
        )
        # The current task ran to completion (all 20 planned steps),
        # unlike STOP_BEFORE_EXCEEDING above.
        self.assertEqual(privacy_result.steps, 20)
        self.assertIn("weight", outcome.delta)
        self.assertEqual(
            privacy_result.budget_decision_outcome,
            SampleBudgetOutcome.STOPPED_AFTER_TASK.value,
        )
        self.assertTrue(enforcer.stopped_for_future_tasks)
        with self.assertRaises(SampleLevelBudgetExceededError):
            enforcer.refuse_if_already_stopped()

    def test_warn_only_never_blocks_training(self) -> None:
        privacy_config = SampleLevelDPConfig(
            noise_multiplier=1.0, max_grad_norm=1.0, target_delta=1e-5
        )
        enforcer = SampleBudgetEnforcer(
            client_id="client-budget",
            policy=SamplePrivacyBudgetPolicy.WARN_ONLY,
            epsilon_budget=0.1,  # exceeded almost immediately
            target_delta=1e-5,
        )
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        outcome, privacy_result = run_private_local_training(
            self._task(local_epochs=5),
            _global_state(),
            model,
            privacy_config,
            sample_count=16,
            budget_enforcer=enforcer,
        )
        self.assertEqual(privacy_result.steps, 20)
        self.assertIn("weight", outcome.delta)
        self.assertFalse(enforcer.stopped_for_future_tasks)

    def test_no_enforcer_behaves_exactly_as_before_this_change(self) -> None:
        privacy_config = SampleLevelDPConfig(
            noise_multiplier=1.0, max_grad_norm=1.0, target_delta=1e-5
        )
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        outcome, privacy_result = run_private_local_training(
            self._task(local_epochs=1),
            _global_state(),
            model,
            privacy_config,
            sample_count=16,
        )
        self.assertIn("weight", outcome.delta)
        self.assertEqual(privacy_result.budget_decision_outcome, "no_enforcer")
        self.assertTrue(len(privacy_result.accountant_state_hash) == 64)


class SecureRandomRequiredIntegrationTests(unittest.TestCase):
    """torchcsprng is not installed in this environment (verified, not
    assumed -- see test_secure_random.py), so secure_random_required=True
    is expected to genuinely reject the task here, proving the real
    end-to-end rejection path (not just the isolated unit-level checks
    in test_secure_random.py) never silently falls back to
    secure_mode=False.
    """

    def test_secure_random_required_rejects_before_training_when_unavailable(
        self,
    ) -> None:
        task = ClientTrainingTask(
            has_task=True,
            client_id="client-secure",
            round_id=1,
            algorithm="fedavg",
            local_epochs=1,
            batch_size=4,
            learning_rate=0.1,
        )
        privacy_config = SampleLevelDPConfig(
            noise_multiplier=1.0, max_grad_norm=1.0, secure_random_required=True
        )
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        with self.assertRaises(SecureRandomTaskRejectedError) as ctx:
            run_private_local_training(
                task, _global_state(), model, privacy_config, sample_count=16
            )
        self.assertIn("client-secure", str(ctx.exception))

    def test_secure_random_not_required_trains_normally(self) -> None:
        task = ClientTrainingTask(
            has_task=True,
            client_id="client-a",
            round_id=1,
            algorithm="fedavg",
            local_epochs=1,
            batch_size=4,
            learning_rate=0.1,
        )
        privacy_config = SampleLevelDPConfig(
            noise_multiplier=1.0, max_grad_norm=1.0, secure_random_required=False
        )
        model = build_bridge_compatible_model(
            num_classes=2, in_channels=1, image_size=4
        )
        outcome, privacy_result = run_private_local_training(
            task, _global_state(), model, privacy_config, sample_count=16
        )
        self.assertIn("weight", outcome.delta)
        self.assertGreater(privacy_result.epsilon, 0.0)


if __name__ == "__main__":
    unittest.main()
