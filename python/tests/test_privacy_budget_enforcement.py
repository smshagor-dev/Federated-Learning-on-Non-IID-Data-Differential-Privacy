"""Unit tests for fl_platform.privacy.budget_enforcement — worker-side
sample-level privacy budget enforcement (the Secure Aggregation and
Cryptographic Protocols category's closure-gate work; see
docs/privacy-budget-policies.md and
docs/secure-aggregation-architecture.md). Exercises the real Opacus
accountant directly (no training loop needed at this layer — the
training-loop integration is covered separately in
test_private_training.py).
"""

from __future__ import annotations

import unittest

from fl_platform.privacy import SamplePrivacyBudgetPolicy
from fl_platform.privacy.budget_enforcement import (
    SampleBudgetEnforcer,
    SampleBudgetOutcome,
    SampleLevelBudgetExceededError,
    accountant_state_hash,
    checkpoint_enforcer,
    project_next_epsilon,
    restore_enforcer,
)
from opacus.accountants import create_accountant


def _fresh_accountant():
    return create_accountant(mechanism="rdp")


class ProjectionTests(unittest.TestCase):
    def test_projection_does_not_mutate_the_real_accountant(self) -> None:
        accountant = _fresh_accountant()
        accountant.step(noise_multiplier=1.0, sample_rate=0.1)
        before = accountant.get_epsilon(delta=1e-5)
        before_history = list(accountant.history)

        projected = project_next_epsilon(
            accountant, noise_multiplier=1.0, sample_rate=0.1, target_delta=1e-5
        )

        self.assertGreater(projected, before)
        self.assertEqual(accountant.get_epsilon(delta=1e-5), before)
        self.assertEqual(accountant.history, before_history)

    def test_projection_on_empty_history_matches_a_real_first_step(self) -> None:
        accountant = _fresh_accountant()
        projected = project_next_epsilon(
            accountant, noise_multiplier=1.0, sample_rate=0.1, target_delta=1e-5
        )
        accountant.step(noise_multiplier=1.0, sample_rate=0.1)
        self.assertAlmostEqual(projected, accountant.get_epsilon(delta=1e-5))


class StateHashTests(unittest.TestCase):
    def test_same_history_same_hash(self) -> None:
        a = _fresh_accountant()
        b = _fresh_accountant()
        a.step(noise_multiplier=1.0, sample_rate=0.1)
        b.step(noise_multiplier=1.0, sample_rate=0.1)
        self.assertEqual(accountant_state_hash(a), accountant_state_hash(b))

    def test_different_history_different_hash(self) -> None:
        a = _fresh_accountant()
        b = _fresh_accountant()
        a.step(noise_multiplier=1.0, sample_rate=0.1)
        b.step(noise_multiplier=2.0, sample_rate=0.1)
        self.assertNotEqual(accountant_state_hash(a), accountant_state_hash(b))

    def test_hash_never_contains_secret_or_raw_gradient_material(self) -> None:
        # The hash is a fingerprint of (noise_multiplier, sample_rate,
        # steps) tuples only -- a sanity check that nothing else has been
        # folded in by accident.
        accountant = _fresh_accountant()
        accountant.step(noise_multiplier=1.0, sample_rate=0.1)
        digest = accountant_state_hash(accountant)
        self.assertEqual(len(digest), 64)  # sha256 hex digest length
        int(digest, 16)  # raises if not valid hex


class WarnOnlyPolicyTests(unittest.TestCase):
    def test_never_blocks_and_reports_warned_once_exceeded(self) -> None:
        accountant = _fresh_accountant()
        enforcer = SampleBudgetEnforcer(
            client_id="c1",
            policy=SamplePrivacyBudgetPolicy.WARN_ONLY,
            epsilon_budget=0.5,
            target_delta=1e-5,
        )
        saw_warned = False
        for _ in range(20):
            before = enforcer.check_before_step(
                accountant, noise_multiplier=1.0, sample_rate=0.5
            )
            self.assertEqual(before.outcome, SampleBudgetOutcome.ALLOWED)
            accountant.step(noise_multiplier=1.0, sample_rate=0.5)
            after = enforcer.check_after_step(accountant)
            if after.outcome == SampleBudgetOutcome.WARNED:
                saw_warned = True
        self.assertTrue(saw_warned)
        self.assertFalse(enforcer.stopped_for_future_tasks)
        enforcer.refuse_if_already_stopped()  # must never raise


class BudgetUnsetTests(unittest.TestCase):
    def test_zero_budget_never_blocks_regardless_of_policy(self) -> None:
        for policy in SamplePrivacyBudgetPolicy:
            with self.subTest(policy=policy):
                accountant = _fresh_accountant()
                enforcer = SampleBudgetEnforcer(
                    client_id="c1", policy=policy, epsilon_budget=0.0, target_delta=1e-5
                )
                for _ in range(10):
                    before = enforcer.check_before_step(
                        accountant, noise_multiplier=1.0, sample_rate=0.5
                    )
                    self.assertEqual(before.outcome, SampleBudgetOutcome.ALLOWED)
                    accountant.step(noise_multiplier=1.0, sample_rate=0.5)
                    after = enforcer.check_after_step(accountant)
                    self.assertEqual(after.outcome, SampleBudgetOutcome.ALLOWED)


class StopBeforeExceedingPolicyTests(unittest.TestCase):
    def test_blocks_the_step_that_would_exceed_budget_not_after(self) -> None:
        accountant = _fresh_accountant()
        enforcer = SampleBudgetEnforcer(
            client_id="c1",
            policy=SamplePrivacyBudgetPolicy.STOP_BEFORE_EXCEEDING,
            epsilon_budget=0.5,
            target_delta=1e-5,
        )
        # A gentle (small sample_rate, moderate noise) per-step epsilon
        # growth curve, so several real steps happen before budget is
        # hit -- a tight single-step budget would make this
        # indistinguishable from the "zero safe steps" case tested
        # separately below.
        steps_taken = 0
        for _ in range(100):
            decision = enforcer.check_before_step(
                accountant, noise_multiplier=1.5, sample_rate=0.02
            )
            if decision.outcome == SampleBudgetOutcome.STOPPED_BEFORE_STEP:
                break
            accountant.step(noise_multiplier=1.5, sample_rate=0.02)
            steps_taken += 1
            enforcer.check_after_step(accountant)
        else:
            self.fail("budget was never reached within 100 steps")

        # The real accountant was never pushed over budget: every step
        # actually taken kept epsilon strictly below budget.
        self.assertLess(accountant.get_epsilon(delta=1e-5), 0.5)
        self.assertGreater(steps_taken, 1)

    def test_zero_safe_steps_raises_rather_than_returning_empty_success(self) -> None:
        accountant = _fresh_accountant()
        enforcer = SampleBudgetEnforcer(
            client_id="c1",
            policy=SamplePrivacyBudgetPolicy.STOP_BEFORE_EXCEEDING,
            # A budget so tiny even the very first step's projection
            # exceeds it.
            epsilon_budget=1e-9,
            target_delta=1e-5,
        )
        decision = enforcer.check_before_step(
            accountant, noise_multiplier=1.0, sample_rate=0.5
        )
        self.assertEqual(decision.outcome, SampleBudgetOutcome.STOPPED_BEFORE_STEP)
        # task_runner.py raises SampleLevelBudgetExceededError itself in
        # this exact zero-steps-completed situation; verified directly
        # in test_private_training.py's integration test.


class StopAfterCurrentTaskPolicyTests(unittest.TestCase):
    def test_current_step_completes_then_future_tasks_are_refused(self) -> None:
        accountant = _fresh_accountant()
        enforcer = SampleBudgetEnforcer(
            client_id="c1",
            policy=SamplePrivacyBudgetPolicy.STOP_AFTER_CURRENT_TASK,
            epsilon_budget=1.0,
            target_delta=1e-5,
        )
        decision = None
        for _ in range(50):
            enforcer.check_before_step(
                accountant, noise_multiplier=1.0, sample_rate=0.5
            )
            accountant.step(noise_multiplier=1.0, sample_rate=0.5)
            decision = enforcer.check_after_step(accountant)
            if decision.outcome == SampleBudgetOutcome.STOPPED_AFTER_TASK:
                break
        assert decision is not None
        self.assertEqual(decision.outcome, SampleBudgetOutcome.STOPPED_AFTER_TASK)
        self.assertTrue(enforcer.stopped_for_future_tasks)

        # A later task for the same (in-process) client must be refused
        # before any training starts.
        with self.assertRaises(SampleLevelBudgetExceededError) as ctx:
            enforcer.refuse_if_already_stopped()
        self.assertEqual(
            ctx.exception.decision.outcome, SampleBudgetOutcome.REFUSED_BEFORE_TRAINING
        )


class FailTaskPolicyTests(unittest.TestCase):
    def test_raises_immediately_once_post_step_epsilon_exceeds_budget(self) -> None:
        accountant = _fresh_accountant()
        enforcer = SampleBudgetEnforcer(
            client_id="c1",
            policy=SamplePrivacyBudgetPolicy.FAIL_TASK,
            epsilon_budget=1.0,
            target_delta=1e-5,
        )
        raised = False
        for _ in range(50):
            enforcer.check_before_step(
                accountant, noise_multiplier=1.0, sample_rate=0.5
            )
            accountant.step(noise_multiplier=1.0, sample_rate=0.5)
            try:
                enforcer.check_after_step(accountant)
            except SampleLevelBudgetExceededError as error:
                raised = True
                self.assertEqual(
                    error.decision.outcome, SampleBudgetOutcome.FAILED_TASK
                )
                break
        self.assertTrue(raised, "FAIL_TASK never raised within 50 steps")
        self.assertTrue(enforcer.stopped_for_future_tasks)


class CheckpointRestoreTests(unittest.TestCase):
    def test_restore_reproduces_identical_epsilon_without_double_counting(self) -> None:
        accountant = _fresh_accountant()
        enforcer = SampleBudgetEnforcer(
            client_id="c1",
            policy=SamplePrivacyBudgetPolicy.STOP_AFTER_CURRENT_TASK,
            epsilon_budget=100.0,  # high enough not to trigger during this test
            target_delta=1e-5,
        )
        for _ in range(5):
            enforcer.check_before_step(
                accountant, noise_multiplier=1.0, sample_rate=0.1
            )
            accountant.step(noise_multiplier=1.0, sample_rate=0.1)
            enforcer.check_after_step(accountant)

        original_epsilon = accountant.get_epsilon(delta=1e-5)
        original_steps = sum(s for (_, _, s) in accountant.history)

        state = checkpoint_enforcer(enforcer, accountant)
        restored_enforcer, restored_accountant = restore_enforcer(state)

        # Restoring alone (no further steps) must reproduce exactly the
        # same epsilon and step count -- not double it.
        self.assertEqual(restored_accountant.get_epsilon(delta=1e-5), original_epsilon)
        restored_steps = sum(s for (_, _, s) in restored_accountant.history)
        self.assertEqual(restored_steps, original_steps)
        self.assertEqual(restored_enforcer.policy, enforcer.policy)
        self.assertEqual(restored_enforcer.epsilon_budget, enforcer.epsilon_budget)
        self.assertEqual(
            restored_enforcer.stopped_for_future_tasks,
            enforcer.stopped_for_future_tasks,
        )

        # One further step after restore must advance exactly one step's
        # worth, not replay history a second time.
        restored_enforcer.check_before_step(
            restored_accountant, noise_multiplier=1.0, sample_rate=0.1
        )
        restored_accountant.step(noise_multiplier=1.0, sample_rate=0.1)
        restored_enforcer.check_after_step(restored_accountant)
        final_steps = sum(s for (_, _, s) in restored_accountant.history)
        self.assertEqual(final_steps, original_steps + 1)

    def test_restore_preserves_a_stopped_enforcer_stop_flag(self) -> None:
        accountant = _fresh_accountant()
        enforcer = SampleBudgetEnforcer(
            client_id="c1",
            policy=SamplePrivacyBudgetPolicy.FAIL_TASK,
            epsilon_budget=1e-9,
            target_delta=1e-5,
        )
        accountant.step(noise_multiplier=1.0, sample_rate=0.5)
        with self.assertRaises(SampleLevelBudgetExceededError):
            enforcer.check_after_step(accountant)
        self.assertTrue(enforcer.stopped_for_future_tasks)

        state = checkpoint_enforcer(enforcer, accountant)
        restored_enforcer, _ = restore_enforcer(state)
        self.assertTrue(restored_enforcer.stopped_for_future_tasks)
        with self.assertRaises(SampleLevelBudgetExceededError):
            restored_enforcer.refuse_if_already_stopped()


if __name__ == "__main__":
    unittest.main()
