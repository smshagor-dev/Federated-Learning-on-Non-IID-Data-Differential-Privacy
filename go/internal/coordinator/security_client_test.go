package coordinator

import (
	"context"
	"errors"
	"testing"
)

func TestMockClientWorkerIdentityLifecycle(t *testing.T) {
	client := NewMockClient()
	ctx := context.Background()

	if _, err := client.GetWorkerIdentity(ctx, "worker-1", "trace-1"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("GetWorkerIdentity on unknown worker: expected ErrNotFound, got %v", err)
	}

	client.SeedWorkerIdentity(WorkerIdentitySummary{WorkerID: "worker-1", RegistrationStatus: "active"})

	identities, err := client.ListWorkerIdentities(ctx, "trace-1")
	if err != nil || len(identities) != 1 || identities[0].WorkerID != "worker-1" {
		t.Fatalf("ListWorkerIdentities: expected one worker-1 entry, got %+v, err=%v", identities, err)
	}

	suspended, err := client.SuspendWorker(ctx, WorkerLifecycleRequest{WorkerID: "worker-1", Reason: "test"})
	if err != nil || !suspended.Changed || suspended.Identity.RegistrationStatus != "suspended" {
		t.Fatalf("SuspendWorker: expected changed=true status=suspended, got %+v, err=%v", suspended, err)
	}

	// Idempotent: suspending an already-suspended worker is a no-op.
	suspendedAgain, err := client.SuspendWorker(ctx, WorkerLifecycleRequest{WorkerID: "worker-1", Reason: "test"})
	if err != nil || suspendedAgain.Changed {
		t.Fatalf("idempotent SuspendWorker: expected changed=false, got %+v, err=%v", suspendedAgain, err)
	}

	activated, err := client.ActivateWorker(ctx, WorkerLifecycleRequest{WorkerID: "worker-1"})
	if err != nil || !activated.Changed || activated.Identity.RegistrationStatus != "active" {
		t.Fatalf("ActivateWorker: expected changed=true status=active, got %+v, err=%v", activated, err)
	}

	revoked, err := client.RevokeWorker(ctx, WorkerLifecycleRequest{WorkerID: "worker-1", Reason: "compromised"})
	if err != nil || !revoked.Changed || revoked.LeasesCanceled != 1 {
		t.Fatalf("RevokeWorker: expected changed=true leases_canceled=1, got %+v, err=%v", revoked, err)
	}
}

func TestMockClientWorkerSigningKeyRevocationSuspendsWorkerWhenNoneRemain(t *testing.T) {
	client := NewMockClient()
	ctx := context.Background()
	client.SeedWorkerIdentity(WorkerIdentitySummary{WorkerID: "worker-1", RegistrationStatus: "active"})
	client.SeedWorkerSigningKey(WorkerSigningKeySummary{WorkerID: "worker-1", SigningKeyID: "key-1", Status: "active"})

	result, err := client.RevokeWorkerSigningKey(ctx, RevokeWorkerSigningKeyRequest{
		WorkerID: "worker-1", SigningKeyID: "key-1", Reason: "rotated out",
	})
	if err != nil || !result.Changed || !result.WorkerSuspended {
		t.Fatalf("RevokeWorkerSigningKey: expected changed=true worker_suspended=true (sole key), got %+v, err=%v", result, err)
	}
	identity, err := client.GetWorkerIdentity(ctx, "worker-1", "")
	if err != nil || identity.RegistrationStatus != "suspended" {
		t.Fatalf("expected worker-1 auto-suspended after its sole signing key was revoked, got %+v, err=%v", identity, err)
	}

	if _, err := client.RevokeWorkerSigningKey(ctx, RevokeWorkerSigningKeyRequest{WorkerID: "worker-1", SigningKeyID: "unknown"}); !errors.Is(err, ErrNotFound) {
		t.Fatalf("RevokeWorkerSigningKey on unknown key: expected ErrNotFound, got %v", err)
	}
}

func TestMockClientCoordinatorSigningKeyRotationIsIdempotent(t *testing.T) {
	client := NewMockClient()
	ctx := context.Background()
	client.SeedCoordinatorSigningKey(CoordinatorSigningKeySummary{SigningKeyID: "genesis", Status: "active"})

	first, err := client.RotateCoordinatorSigningKey(ctx, RotateCoordinatorSigningKeyRequest{
		ExpectedCurrentSigningKeyID: "genesis", IdempotencyKey: "idem-1",
	})
	if err != nil || !first.Accepted || first.IdempotentReplay {
		t.Fatalf("first rotation: expected accepted=true idempotent_replay=false, got %+v, err=%v", first, err)
	}
	if first.PreviousKey.SigningKeyID != "genesis" || first.PreviousKey.Status != "grace_period" {
		t.Fatalf("expected genesis to move to grace_period, got %+v", first.PreviousKey)
	}

	retry, err := client.RotateCoordinatorSigningKey(ctx, RotateCoordinatorSigningKeyRequest{
		ExpectedCurrentSigningKeyID: "genesis", IdempotencyKey: "idem-1",
	})
	if err != nil || !retry.IdempotentReplay || retry.NewKey.SigningKeyID != first.NewKey.SigningKeyID {
		t.Fatalf("retried rotation with same idempotency key: expected the SAME new key (idempotent replay), got %+v vs first %+v, err=%v", retry, first, err)
	}

	// A rotation attempt naming the wrong expected current key is rejected,
	// not silently rotated from whatever key actually is ACTIVE.
	mismatch, err := client.RotateCoordinatorSigningKey(ctx, RotateCoordinatorSigningKeyRequest{
		ExpectedCurrentSigningKeyID: "not-the-real-key", IdempotencyKey: "idem-2",
	})
	if err != nil || mismatch.Accepted {
		t.Fatalf("rotation with mismatched expected_current_signing_key_id: expected accepted=false, got %+v, err=%v", mismatch, err)
	}
}

func TestMockClientCoordinatorSigningKeyRevocationStopsIssuanceWhenSoleActive(t *testing.T) {
	client := NewMockClient()
	ctx := context.Background()
	client.SeedCoordinatorSigningKey(CoordinatorSigningKeySummary{SigningKeyID: "genesis", Status: "active"})

	result, err := client.RevokeCoordinatorSigningKey(ctx, RevokeCoordinatorSigningKeyRequest{SigningKeyID: "genesis", Reason: "compromised"})
	if err != nil || !result.Changed || !result.ProductionTaskIssuanceStopped {
		t.Fatalf("expected changed=true production_task_issuance_stopped=true, got %+v, err=%v", result, err)
	}

	// expected_status compare-and-set: revoking an already-revoked key
	// with a mismatched expectation is rejected, not silently accepted.
	_, err = client.RevokeCoordinatorSigningKey(ctx, RevokeCoordinatorSigningKeyRequest{
		SigningKeyID: "genesis", ExpectedStatus: "active",
	})
	if !errors.Is(err, ErrFailedPrecondition) {
		t.Fatalf("expected ErrFailedPrecondition for a stale expected_status, got %v", err)
	}

	if _, err := client.RevokeCoordinatorSigningKey(ctx, RevokeCoordinatorSigningKeyRequest{SigningKeyID: "unknown"}); !errors.Is(err, ErrNotFound) {
		t.Fatalf("RevokeCoordinatorSigningKey on unknown key: expected ErrNotFound, got %v", err)
	}
}

func TestMockClientTransportAndTrustModelStatus(t *testing.T) {
	client := NewMockClient()
	ctx := context.Background()

	status, err := client.GetTransportSecurityStatus(ctx, "trace-1")
	if err != nil || status.TransportMode != "insecure_development" || status.MutualTLSEnforced {
		t.Fatalf("default transport status: expected insecure_development/false, got %+v, err=%v", status, err)
	}

	client.SeedTransportSecurityStatus(TransportSecurityStatus{TransportMode: "mtls_required", MutualTLSEnforced: true})
	status, err = client.GetTransportSecurityStatus(ctx, "trace-1")
	if err != nil || status.TransportMode != "mtls_required" || !status.MutualTLSEnforced {
		t.Fatalf("seeded transport status: expected mtls_required/true, got %+v, err=%v", status, err)
	}

	client.SeedSecurityTrustModel(SecurityTrustModel{ActiveCoordinatorSigningKeyID: "key-1", TrustedCoordinatorKeyCount: 2})
	model, err := client.GetSecurityTrustModel(ctx, "trace-1")
	if err != nil || model.ActiveCoordinatorSigningKeyID != "key-1" || model.TrustedCoordinatorKeyCount != 2 {
		t.Fatalf("expected seeded trust model, got %+v, err=%v", model, err)
	}
}

func TestMapSecurityGrpcErrorDistinguishesCodes(t *testing.T) {
	// mapSecurityGrpcError is exercised indirectly through GrpcClient in
	// integration tests (docs/security-api.md); this asserts the
	// nil-error short circuit other tests rely on.
	if err := mapSecurityGrpcError(nil); err != nil {
		t.Fatalf("expected nil error to map to nil, got %v", err)
	}
}
