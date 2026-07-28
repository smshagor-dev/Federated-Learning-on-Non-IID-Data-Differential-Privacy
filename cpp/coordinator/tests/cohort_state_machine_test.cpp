#include "fl_coordinator/secure_aggregation_session.hpp"
#include "test_support.hpp"

namespace fl::coordinator::testing {

void run_cohort_state_machine_tests() {
    using fl::coordinator::CohortState;
    using fl::coordinator::CohortStateMachine;
    using fl::coordinator::CohortStateMachineError;
    using fl::coordinator::SecureAggregationAbortReason;
    using fl::coordinator::SecureAggregationProvider;

    // Provider names communicate the limitation, never claim completeness.
    {
        check(to_string(SecureAggregationProvider::kNone) == "NONE",
              "SecureAggregationProvider::kNone stringifies as NONE");
        check(to_string(SecureAggregationProvider::kSecureAggregationNoDropoutExperimental) ==
                  "SECAGG_NO_DROPOUT_EXPERIMENTAL",
              "the real provider's name communicates the no-dropout/experimental limitation "
              "directly");
    }

    // A fresh state machine starts in CohortForming, non-terminal, no history.
    {
        CohortStateMachine machine("session-1");
        check(machine.session_id() == "session-1", "session_id is preserved");
        check(machine.state() == CohortState::kCohortForming,
              "a fresh state machine starts in COHORT_FORMING");
        check(!machine.is_terminal(), "COHORT_FORMING is not a terminal state");
        check(machine.history().empty(), "a fresh state machine has no transition history");
        check(machine.abort_reason() == SecureAggregationAbortReason::kNone,
              "a fresh, non-aborted state machine has no abort reason");
    }

    // The full happy-path forward sequence, one step at a time.
    {
        CohortStateMachine machine("session-2");
        machine.transition_to(CohortState::kKeyAdvertisement, 1.0, "keys advertised");
        check(machine.state() == CohortState::kKeyAdvertisement,
              "COHORT_FORMING -> KEY_ADVERTISEMENT succeeds");

        machine.transition_to(CohortState::kCohortFrozen, 2.0, "cohort frozen");
        check(machine.state() == CohortState::kCohortFrozen,
              "KEY_ADVERTISEMENT -> COHORT_FROZEN succeeds");

        machine.transition_to(
            CohortState::kMaskedUpdateCollection, 3.0, "collecting masked updates");
        check(machine.state() == CohortState::kMaskedUpdateCollection,
              "COHORT_FROZEN -> MASKED_UPDATE_COLLECTION succeeds");

        machine.transition_to(CohortState::kAggregateValidation, 4.0, "validating aggregate");
        check(machine.state() == CohortState::kAggregateValidation,
              "MASKED_UPDATE_COLLECTION -> AGGREGATE_VALIDATION succeeds");

        machine.transition_to(CohortState::kCompleted, 5.0, "done");
        check(machine.state() == CohortState::kCompleted,
              "AGGREGATE_VALIDATION -> COMPLETED succeeds");
        check(machine.is_terminal(), "COMPLETED is a terminal state");
        check(machine.history().size() == 5, "every forward transition is recorded in history");

        expect_throw([&]() { machine.transition_to(CohortState::kKeyAdvertisement, 6.0); },
                     "no transition is possible out of a terminal COMPLETED state");
    }

    // Skipping a state is rejected -- no implicit shortcuts.
    {
        CohortStateMachine machine("session-3");
        expect_throw(
            [&]() { machine.transition_to(CohortState::kCohortFrozen, 1.0); },
            "COHORT_FORMING cannot jump directly to COHORT_FROZEN, skipping KEY_ADVERTISEMENT");
    }

    // Going backwards is rejected.
    {
        CohortStateMachine machine("session-4");
        machine.transition_to(CohortState::kKeyAdvertisement, 1.0);
        machine.transition_to(CohortState::kCohortFrozen, 2.0);
        expect_throw([&]() { machine.transition_to(CohortState::kKeyAdvertisement, 3.0); },
                     "COHORT_FROZEN cannot transition backwards to KEY_ADVERTISEMENT");
    }

    // Abort is reachable from any non-terminal state, always with a
    // specific reason -- this is the mechanism the no-dropout policy
    // relies on: any required participant missing after freeze aborts,
    // it never silently continues with a partial cohort.
    {
        CohortStateMachine forming("session-5a");
        forming.abort(
            SecureAggregationAbortReason::kManualAbort, 1.0, "operator cancelled before freeze");
        check(forming.state() == CohortState::kAborted, "COHORT_FORMING can abort directly");
        check(forming.abort_reason() == SecureAggregationAbortReason::kManualAbort,
              "the abort reason is recorded");

        CohortStateMachine frozen("session-5b");
        frozen.transition_to(CohortState::kKeyAdvertisement, 1.0);
        frozen.transition_to(CohortState::kCohortFrozen, 2.0);
        frozen.transition_to(CohortState::kMaskedUpdateCollection, 3.0);
        frozen.abort(
            SecureAggregationAbortReason::kDropout, 4.0, "worker-3 did not submit before deadline");
        check(frozen.state() == CohortState::kAborted,
              "MASKED_UPDATE_COLLECTION aborts on a post-freeze dropout, never proceeds with a "
              "partial cohort");
        check(frozen.abort_reason() == SecureAggregationAbortReason::kDropout,
              "the dropout abort reason is recorded distinctly from a manual abort");

        expect_throw([&]() { frozen.abort(SecureAggregationAbortReason::kManualAbort, 5.0); },
                     "an already-aborted session cannot be aborted again");
        expect_throw([&]() { frozen.transition_to(CohortState::kAggregateValidation, 5.0); },
                     "no forward transition is possible out of an aborted session");
    }

    // abort() requires a specific reason -- kNone is a caller error.
    {
        CohortStateMachine machine("session-6");
        expect_throw([&]() { machine.abort(SecureAggregationAbortReason::kNone, 1.0); },
                     "abort() rejects SecureAggregationAbortReason::kNone as a caller error");
    }

    // fail() is unconditional -- reachable even mid-transition-attempt,
    // and marks the machine terminal with a recorded reason.
    {
        CohortStateMachine machine("session-7");
        machine.transition_to(CohortState::kKeyAdvertisement, 1.0);
        machine.fail("unexpected internal error: manifest hash mismatch mid-round", 2.0);
        check(machine.state() == CohortState::kFailed,
              "fail() moves the machine to FAILED regardless of prior state");
        check(machine.is_terminal(), "FAILED is a terminal state");
        check(machine.failure_reason() ==
                  "unexpected internal error: manifest hash mismatch mid-round",
              "the failure reason is recorded verbatim");
    }

    // Every CohortState and SecureAggregationAbortReason value stringifies
    // to a non-empty, recognizable label (no "unknown" for a real value).
    {
        check(to_string(CohortState::kCohortForming) == "COHORT_FORMING",
              "CohortState::kCohortForming stringifies as expected");
        check(to_string(CohortState::kAborted) == "ABORTED",
              "CohortState::kAborted stringifies as expected");
        check(to_string(CohortState::kFailed) == "FAILED",
              "CohortState::kFailed stringifies as expected");
        check(to_string(SecureAggregationAbortReason::kDropout) == "dropout",
              "SecureAggregationAbortReason::kDropout stringifies as expected");
    }
}

}  // namespace fl::coordinator::testing
