"""Tests for fl_platform.secure_aggregation.cohort_state_machine --
mirrors cpp/coordinator/tests/cohort_state_machine_test.cpp case-for-
case.
"""

from __future__ import annotations

import unittest

from fl_platform.secure_aggregation.cohort_state_machine import (
    ABORT_REASON_DROPOUT,
    ABORT_REASON_MANUAL_ABORT,
    ABORT_REASON_NONE,
    PROVIDER_NONE,
    PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL,
    STATE_AGGREGATE_VALIDATION,
    STATE_COHORT_FORMING,
    STATE_COHORT_FROZEN,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_KEY_ADVERTISEMENT,
    STATE_MASKED_UPDATE_COLLECTION,
    CohortStateMachine,
    CohortStateMachineError,
)


class CohortStateMachineTests(unittest.TestCase):
    def test_provider_names_communicate_the_limitation(self) -> None:
        self.assertEqual(PROVIDER_NONE, "NONE")
        self.assertEqual(
            PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL, "SECAGG_NO_DROPOUT_EXPERIMENTAL"
        )

    def test_fresh_machine_starts_in_cohort_forming(self) -> None:
        machine = CohortStateMachine("session-1")
        self.assertEqual(machine.session_id, "session-1")
        self.assertEqual(machine.state, STATE_COHORT_FORMING)
        self.assertFalse(machine.is_terminal)
        self.assertEqual(machine.history, [])
        self.assertEqual(machine.abort_reason, ABORT_REASON_NONE)

    def test_full_happy_path_forward_sequence(self) -> None:
        machine = CohortStateMachine("session-2")
        machine.transition_to(STATE_KEY_ADVERTISEMENT, 1.0, "keys advertised")
        self.assertEqual(machine.state, STATE_KEY_ADVERTISEMENT)

        machine.transition_to(STATE_COHORT_FROZEN, 2.0, "cohort frozen")
        self.assertEqual(machine.state, STATE_COHORT_FROZEN)

        machine.transition_to(STATE_MASKED_UPDATE_COLLECTION, 3.0)
        self.assertEqual(machine.state, STATE_MASKED_UPDATE_COLLECTION)

        machine.transition_to(STATE_AGGREGATE_VALIDATION, 4.0)
        self.assertEqual(machine.state, STATE_AGGREGATE_VALIDATION)

        machine.transition_to(STATE_COMPLETED, 5.0)
        self.assertEqual(machine.state, STATE_COMPLETED)
        self.assertTrue(machine.is_terminal)
        self.assertEqual(len(machine.history), 5)

        with self.assertRaises(CohortStateMachineError):
            machine.transition_to(STATE_KEY_ADVERTISEMENT, 6.0)

    def test_skipping_a_state_is_rejected(self) -> None:
        machine = CohortStateMachine("session-3")
        with self.assertRaises(CohortStateMachineError):
            machine.transition_to(STATE_COHORT_FROZEN, 1.0)

    def test_going_backwards_is_rejected(self) -> None:
        machine = CohortStateMachine("session-4")
        machine.transition_to(STATE_KEY_ADVERTISEMENT, 1.0)
        machine.transition_to(STATE_COHORT_FROZEN, 2.0)
        with self.assertRaises(CohortStateMachineError):
            machine.transition_to(STATE_KEY_ADVERTISEMENT, 3.0)

    def test_abort_reachable_from_any_non_terminal_state(self) -> None:
        forming = CohortStateMachine("session-5a")
        forming.abort(
            ABORT_REASON_MANUAL_ABORT, 1.0, "operator cancelled before freeze"
        )
        self.assertEqual(forming.state, "ABORTED")
        self.assertEqual(forming.abort_reason, ABORT_REASON_MANUAL_ABORT)

        frozen = CohortStateMachine("session-5b")
        frozen.transition_to(STATE_KEY_ADVERTISEMENT, 1.0)
        frozen.transition_to(STATE_COHORT_FROZEN, 2.0)
        frozen.transition_to(STATE_MASKED_UPDATE_COLLECTION, 3.0)
        frozen.abort(
            ABORT_REASON_DROPOUT, 4.0, "worker-3 did not submit before deadline"
        )
        self.assertEqual(frozen.state, "ABORTED")
        self.assertEqual(frozen.abort_reason, ABORT_REASON_DROPOUT)

        with self.assertRaises(CohortStateMachineError):
            frozen.abort(ABORT_REASON_MANUAL_ABORT, 5.0)
        with self.assertRaises(CohortStateMachineError):
            frozen.transition_to(STATE_AGGREGATE_VALIDATION, 5.0)

    def test_abort_requires_a_specific_reason(self) -> None:
        machine = CohortStateMachine("session-6")
        with self.assertRaises(CohortStateMachineError):
            machine.abort(ABORT_REASON_NONE, 1.0)

    def test_fail_is_unconditional(self) -> None:
        machine = CohortStateMachine("session-7")
        machine.transition_to(STATE_KEY_ADVERTISEMENT, 1.0)
        machine.fail("unexpected internal error: manifest hash mismatch mid-round", 2.0)
        self.assertEqual(machine.state, STATE_FAILED)
        self.assertTrue(machine.is_terminal)
        self.assertEqual(
            machine.failure_reason,
            "unexpected internal error: manifest hash mismatch mid-round",
        )


if __name__ == "__main__":
    unittest.main()
