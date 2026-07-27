package observability

import "testing"

func TestValidateSecurityEventMinimalIsValid(t *testing.T) {
	event := SecurityEvent{SchemaVersion: SecurityEventSchemaVersion, SourceService: "coordinator"}
	if ok, reason := ValidateSecurityEvent(event); !ok {
		t.Fatalf("expected valid, got invalid: %s", reason)
	}
}

func TestValidateSecurityEventMissingSourceService(t *testing.T) {
	event := SecurityEvent{SchemaVersion: SecurityEventSchemaVersion}
	if ok, _ := ValidateSecurityEvent(event); ok {
		t.Fatalf("expected invalid for empty source_service")
	}
}

func TestValidateSecurityEventUnrecognizedSeverity(t *testing.T) {
	event := SecurityEvent{SchemaVersion: SecurityEventSchemaVersion, SourceService: "coordinator", Severity: "NOT_REAL"}
	if ok, _ := ValidateSecurityEvent(event); ok {
		t.Fatalf("expected invalid for unrecognized severity")
	}
}

func TestValidateSecurityEventReasonCodeBound(t *testing.T) {
	event := SecurityEvent{
		SchemaVersion: SecurityEventSchemaVersion,
		SourceService: "go-api",
		ReasonCode:    stringOfLength(MaxReasonCodeLength + 1),
	}
	if ok, _ := ValidateSecurityEvent(event); ok {
		t.Fatalf("expected invalid for over-length reason_code")
	}
}

func TestValidateSecurityEventTooManyDetailKeys(t *testing.T) {
	details := map[string]string{}
	for i := 0; i < MaxDetailKeys+1; i++ {
		details[stringOfLength(i+1)] = "v"
	}
	event := SecurityEvent{SchemaVersion: SecurityEventSchemaVersion, SourceService: "go-api", SafeDetails: details}
	if ok, _ := ValidateSecurityEvent(event); ok {
		t.Fatalf("expected invalid for too many safe_details keys")
	}
}

func TestDefaultSeverityMapping(t *testing.T) {
	cases := map[string]string{
		EventCoordinatorTaskSigningFailed: SeverityCritical,
		EventMessageReplayRejected:        SeverityHigh,
		EventWorkerSuspended:              SeverityWarning,
		EventWorkerRegistered:             SeverityInfo,
	}
	for eventType, want := range cases {
		if got := DefaultSeverity(eventType); got != want {
			t.Fatalf("DefaultSeverity(%s) = %s, want %s", eventType, got, want)
		}
	}
}

func TestComputeSecurityEventChecksumDeterministic(t *testing.T) {
	event := fixtureSecurityEvent()
	a := computeSecurityEventChecksum(event)
	b := computeSecurityEventChecksum(event)
	if a != b {
		t.Fatalf("checksum not deterministic: %s vs %s", a, b)
	}
}

func TestComputeSecurityEventChecksumSensitiveToFieldChange(t *testing.T) {
	event := fixtureSecurityEvent()
	original := computeSecurityEventChecksum(event)
	event.ReasonCode = "different_reason"
	if computeSecurityEventChecksum(event) == original {
		t.Fatalf("expected checksum to change when reason_code changes")
	}
}

func TestComputeSecurityEventChecksumExcludesEventIDAndTimestamp(t *testing.T) {
	event := fixtureSecurityEvent()
	original := computeSecurityEventChecksum(event)
	event.EventID = "00000000000000000042"
	event.Timestamp = "2026-01-01T00:00:00Z"
	if computeSecurityEventChecksum(event) != original {
		t.Fatalf("expected event_id/timestamp to be excluded from the checksum")
	}
}

// TestCrossLanguageGoldenFixture: the same fixture event and expected
// checksum used by cpp/coordinator/tests/security_event_test.cpp and
// python/tests/test_security_event.py's cross-language golden fixture --
// independently computed on both of those sides from a real C++ program
// linked against fl_coordinator, not a tautological self-check here.
func TestCrossLanguageGoldenFixture(t *testing.T) {
	event := fixtureSecurityEvent()
	expectedJSON := `{"actor_type":"SERVICE","event_type":"WORKER_SUSPENDED","outcome":"COMPLETED",` +
		`"reason_code":"administrative_suspension","request_id":"","round_id":0,"run_id":"",` +
		`"safe_actor_id":"go-api","safe_details":{},"safe_signing_key_id":"",` +
		`"safe_subject_id":"worker-1","schema_version":1,"severity":"WARNING",` +
		`"source_component":"worker_registry","source_service":"coordinator",` +
		`"subject_type":"WORKER_IDENTITY","task_id":"","trace_id":"","worker_id":"worker-1"}`
	expectedChecksum := "2a1507521d258521"
	if got := string(canonicalSecurityEventPayload(event)); got != expectedJSON {
		t.Fatalf("canonical payload mismatch:\n got:  %s\n want: %s", got, expectedJSON)
	}
	if got := computeSecurityEventChecksum(event); got != expectedChecksum {
		t.Fatalf("checksum mismatch: got %s, want %s", got, expectedChecksum)
	}
}

func fixtureSecurityEvent() SecurityEvent {
	return SecurityEvent{
		SchemaVersion:   SecurityEventSchemaVersion,
		SourceService:   "coordinator",
		SourceComponent: "worker_registry",
		EventType:       EventWorkerSuspended,
		Severity:        SeverityWarning,
		ActorType:       ActorTypeService,
		SafeActorID:     "go-api",
		SubjectType:     SubjectTypeWorkerIdentity,
		SafeSubjectID:   "worker-1",
		WorkerID:        "worker-1",
		Outcome:         OutcomeCompleted,
		ReasonCode:      "administrative_suspension",
		SafeDetails:     map[string]string{},
	}
}

func stringOfLength(n int) string {
	out := make([]byte, n)
	for i := range out {
		out[i] = 'x'
	}
	return string(out)
}
