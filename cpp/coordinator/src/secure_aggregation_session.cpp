#include "fl_coordinator/secure_aggregation_session.hpp"

#include <utility>

namespace fl::coordinator {

std::string to_string(SecureAggregationProvider provider) {
    switch (provider) {
        case SecureAggregationProvider::kNone:
            return "NONE";
        case SecureAggregationProvider::kSecureAggregationNoDropoutExperimental:
            return "SECAGG_NO_DROPOUT_EXPERIMENTAL";
    }
    return "UNKNOWN";
}

std::string to_string(CohortState state) {
    switch (state) {
        case CohortState::kCohortForming:
            return "COHORT_FORMING";
        case CohortState::kKeyAdvertisement:
            return "KEY_ADVERTISEMENT";
        case CohortState::kCohortFrozen:
            return "COHORT_FROZEN";
        case CohortState::kMaskedUpdateCollection:
            return "MASKED_UPDATE_COLLECTION";
        case CohortState::kAggregateValidation:
            return "AGGREGATE_VALIDATION";
        case CohortState::kCompleted:
            return "COMPLETED";
        case CohortState::kAborted:
            return "ABORTED";
        case CohortState::kFailed:
            return "FAILED";
    }
    return "UNKNOWN";
}

std::string to_string(SecureAggregationAbortReason reason) {
    switch (reason) {
        case SecureAggregationAbortReason::kNone:
            return "none";
        case SecureAggregationAbortReason::kDropout:
            return "dropout";
        case SecureAggregationAbortReason::kDeadlineExceeded:
            return "deadline_exceeded";
        case SecureAggregationAbortReason::kCohortMismatch:
            return "cohort_mismatch";
        case SecureAggregationAbortReason::kEncodingRejected:
            return "encoding_rejected";
        case SecureAggregationAbortReason::kOverflowRejected:
            return "overflow_rejected";
        case SecureAggregationAbortReason::kMaskCancellationFailed:
            return "mask_cancellation_failed";
        case SecureAggregationAbortReason::kCoordinatorRestart:
            return "coordinator_restart";
        case SecureAggregationAbortReason::kSessionExpired:
            return "session_expired";
        case SecureAggregationAbortReason::kManualAbort:
            return "manual_abort";
        case SecureAggregationAbortReason::kInvalidTransitionRequested:
            return "invalid_transition_requested";
        case SecureAggregationAbortReason::kPrivacyModeIncompatible:
            return "privacy_mode_incompatible";
    }
    return "unknown";
}

CohortStateMachineError::CohortStateMachineError(const std::string& what)
    : std::runtime_error(what) {}

namespace {

// The one, explicit forward-progress allow-list (Work Package D): every
// legal (from, to) pair for transition_to(). Deliberately a flat list of
// pairs rather than a "next state" map, so a future added state cannot
// silently become reachable from somewhere it shouldn't just because it
// was appended to an enum.
bool is_allowed_forward_transition(CohortState from, CohortState to) {
    switch (from) {
        case CohortState::kCohortForming:
            return to == CohortState::kKeyAdvertisement;
        case CohortState::kKeyAdvertisement:
            return to == CohortState::kCohortFrozen;
        case CohortState::kCohortFrozen:
            return to == CohortState::kMaskedUpdateCollection;
        case CohortState::kMaskedUpdateCollection:
            return to == CohortState::kAggregateValidation;
        case CohortState::kAggregateValidation:
            return to == CohortState::kCompleted;
        case CohortState::kCompleted:
        case CohortState::kAborted:
        case CohortState::kFailed:
            return false;  // terminal: no forward transition out
    }
    return false;
}

bool is_terminal_state(CohortState state) {
    return state == CohortState::kCompleted || state == CohortState::kAborted ||
           state == CohortState::kFailed;
}

}  // namespace

CohortStateMachine::CohortStateMachine(std::string session_id)
    : session_id_(std::move(session_id)), state_(CohortState::kCohortForming) {}

const std::string& CohortStateMachine::session_id() const {
    return session_id_;
}

CohortState CohortStateMachine::state() const {
    return state_;
}

bool CohortStateMachine::is_terminal() const {
    return is_terminal_state(state_);
}

const std::vector<CohortStateTransition>& CohortStateMachine::history() const {
    return history_;
}

SecureAggregationAbortReason CohortStateMachine::abort_reason() const {
    return abort_reason_;
}

const std::string& CohortStateMachine::failure_reason() const {
    return failure_reason_;
}

void CohortStateMachine::transition_to(CohortState next,
                                       double timestamp_unix_s,
                                       const std::string& reason) {
    if (!is_allowed_forward_transition(state_, next)) {
        throw CohortStateMachineError(
            "CohortStateMachine[" + session_id_ + "]: illegal transition from " +
            to_string(state_) + " to " + to_string(next) +
            " (forward progress only, one step at a time, never out of a terminal state)");
    }
    history_.push_back(CohortStateTransition{state_, next, timestamp_unix_s, reason});
    state_ = next;
}

void CohortStateMachine::abort(SecureAggregationAbortReason reason,
                               double timestamp_unix_s,
                               const std::string& detail) {
    if (is_terminal_state(state_)) {
        throw CohortStateMachineError("CohortStateMachine[" + session_id_ +
                                      "]: cannot abort a session already in terminal state " +
                                      to_string(state_));
    }
    if (reason == SecureAggregationAbortReason::kNone) {
        throw CohortStateMachineError(
            "CohortStateMachine[" + session_id_ +
            "]: abort() requires a specific SecureAggregationAbortReason, not kNone");
    }
    history_.push_back(CohortStateTransition{
        state_,
        CohortState::kAborted,
        timestamp_unix_s,
        "abort:" + to_string(reason) + (detail.empty() ? "" : (" - " + detail))});
    state_ = CohortState::kAborted;
    abort_reason_ = reason;
}

void CohortStateMachine::fail(const std::string& reason, double timestamp_unix_s) {
    // Deliberately unconditional: a FAILED marking records an
    // unexpected internal error and must never itself be blocked by
    // the state machine's own transition table (Work Package D).
    history_.push_back(
        CohortStateTransition{state_, CohortState::kFailed, timestamp_unix_s, reason});
    state_ = CohortState::kFailed;
    failure_reason_ = reason;
}

}  // namespace fl::coordinator
