#include "fl_coordinator/security_event.hpp"
#include "test_support.hpp"

namespace fl::coordinator::testing {

void run_security_event_tests() {
    using fl::coordinator::SecurityActorType;
    using fl::coordinator::SecurityEvent;
    using fl::coordinator::SecurityEventType;
    using fl::coordinator::SecurityOutcome;
    using fl::coordinator::SecuritySeverity;
    using fl::coordinator::SecuritySubjectType;

    // Enum round-trip: to_string/from_string agree for every value that
    // matters to persistence.
    {
        SecurityEventType parsed{};
        check(security_event_type_from_string("WORKER_REVOKED", parsed) &&
                  parsed == SecurityEventType::kWorkerRevoked,
              "WORKER_REVOKED round-trips through from_string");
        check(to_string(SecurityEventType::kWorkerRevoked) == "WORKER_REVOKED",
              "kWorkerRevoked stringifies to WORKER_REVOKED");
        check(!security_event_type_from_string("NOT_A_REAL_EVENT_TYPE", parsed),
              "an unrecognized event type string is rejected, not defaulted");

        SecuritySeverity severity{};
        check(security_severity_from_string("CRITICAL", severity) &&
                  severity == SecuritySeverity::kCritical,
              "CRITICAL severity round-trips");

        SecurityOutcome outcome{};
        check(security_outcome_from_string("BLOCKED", outcome) &&
                  outcome == SecurityOutcome::kBlocked,
              "BLOCKED outcome round-trips");

        SecurityActorType actor{};
        check(
            security_actor_type_from_string("WORKER", actor) && actor == SecurityActorType::kWorker,
            "WORKER actor type round-trips");

        SecuritySubjectType subject{};
        check(security_subject_type_from_string("COORDINATOR_SIGNING_KEY", subject) &&
                  subject == SecuritySubjectType::kCoordinatorSigningKey,
              "COORDINATOR_SIGNING_KEY subject type round-trips");
    }

    // Default severity mapping is stable and non-empty for a representative sample.
    {
        check(default_severity(SecurityEventType::kCoordinatorTaskSigningFailed) ==
                  SecuritySeverity::kCritical,
              "task-signing failure defaults to CRITICAL");
        check(
            default_severity(SecurityEventType::kMessageReplayRejected) == SecuritySeverity::kHigh,
            "replay rejection defaults to HIGH");
        check(default_severity(SecurityEventType::kWorkerSuspended) == SecuritySeverity::kWarning,
              "worker suspension defaults to WARNING");
        check(default_severity(SecurityEventType::kWorkerRegistered) == SecuritySeverity::kInfo,
              "worker registration defaults to INFO");
    }

    // Validation bounds.
    {
        SecurityEvent event;
        event.source_service = "coordinator";
        event.event_type = SecurityEventType::kWorkerRegistered;
        const auto ok = validate_security_event(event);
        check(ok.valid, "a minimal, well-formed event validates");

        SecurityEvent missing_service;
        missing_service.source_service = "";
        check(!validate_security_event(missing_service).valid,
              "an event with no source_service fails validation");

        SecurityEvent long_reason;
        long_reason.source_service = "coordinator";
        long_reason.reason_code = std::string(kSecurityEventMaxReasonCodeLength + 1, 'x');
        check(!validate_security_event(long_reason).valid,
              "a reason_code past the length bound fails validation");

        SecurityEvent too_many_details;
        too_many_details.source_service = "coordinator";
        for (std::size_t i = 0; i < kSecurityEventMaxDetailKeys + 1; ++i) {
            too_many_details.safe_details["k" + std::to_string(i)] = "v";
        }
        check(!validate_security_event(too_many_details).valid,
              "an event with too many safe_details keys fails validation");

        SecurityEvent long_detail_value;
        long_detail_value.source_service = "coordinator";
        long_detail_value.safe_details["k"] =
            std::string(kSecurityEventMaxDetailValueLength + 1, 'x');
        check(!validate_security_event(long_detail_value).valid,
              "a safe_details value past the length bound fails validation");
    }

    // Canonical JSON / checksum determinism and sensitivity.
    {
        SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "worker_registry";
        event.event_type = SecurityEventType::kWorkerSuspended;
        event.severity = SecuritySeverity::kWarning;
        event.actor_type = SecurityActorType::kService;
        event.safe_actor_id = "go-api";
        event.subject_type = SecuritySubjectType::kWorkerIdentity;
        event.safe_subject_id = "worker-1";
        event.worker_id = "worker-1";
        event.outcome = SecurityOutcome::kAccepted;
        event.reason_code = "administrative_suspension";

        const auto checksum_a = compute_security_event_checksum(event);
        const auto checksum_b = compute_security_event_checksum(event);
        check(checksum_a == checksum_b, "checksum is deterministic for identical fields");

        SecurityEvent mutated = event;
        mutated.reason_code = "different_reason";
        check(compute_security_event_checksum(mutated) != checksum_a,
              "changing a checksummed field changes the checksum");

        SecurityEvent with_id = event;
        with_id.event_id = "00000000000000000042";
        with_id.timestamp = "2026-01-01T00:00:00Z";
        check(compute_security_event_checksum(with_id) == checksum_a,
              "event_id/timestamp are excluded from the checksum by design");

        const auto payload_json = canonical_security_event_payload_json(event);
        check(payload_json.find("\"event_id\"") == std::string::npos,
              "canonical payload never includes event_id");
        check(payload_json.find("\"payload_checksum\"") == std::string::npos,
              "canonical payload never includes payload_checksum");
    }
}

}  // namespace fl::coordinator::testing
