package coordinator

import (
	"context"
	"math"
	"testing"

	"google.golang.org/grpc"

	coordinatorv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/coordinator/v1"
	privacyv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/privacy/v1"
	workerv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/worker/v1"
)

// privacyStub records CreateRun's privacy_config and serves canned
// responses for the three read-only privacy RPCs — mirrors
// recordingStub's "embed nil interface, implement only what's exercised"
// pattern from grpc_client_test.go.
type privacyStub struct {
	coordinatorv1.CoordinatorServiceClient
	lastCreateRunRequest *coordinatorv1.CreateRunRequest
	metricsResponse      *privacyv1.PrivacyMetricsSnapshot
	ledgerResponse       *coordinatorv1.GetPrivacyLedgerResponse
	projectionResponse   *coordinatorv1.PrivacyProjection
	listWorkersResponse  *coordinatorv1.ListWorkersResponse
}

func (s *privacyStub) ListWorkers(_ context.Context, _ *coordinatorv1.ListWorkersRequest, _ ...grpc.CallOption) (*coordinatorv1.ListWorkersResponse, error) {
	return s.listWorkersResponse, nil
}

func (s *privacyStub) CreateRun(_ context.Context, in *coordinatorv1.CreateRunRequest, _ ...grpc.CallOption) (*coordinatorv1.CreateRunResponse, error) {
	s.lastCreateRunRequest = in
	return &coordinatorv1.CreateRunResponse{RunId: in.GetConfig().GetRunId(), State: "CREATED"}, nil
}

func (s *privacyStub) GetPrivacyMetrics(_ context.Context, _ *coordinatorv1.GetPrivacyMetricsRequest, _ ...grpc.CallOption) (*privacyv1.PrivacyMetricsSnapshot, error) {
	return s.metricsResponse, nil
}

func (s *privacyStub) GetPrivacyLedger(_ context.Context, _ *coordinatorv1.GetPrivacyLedgerRequest, _ ...grpc.CallOption) (*coordinatorv1.GetPrivacyLedgerResponse, error) {
	return s.ledgerResponse, nil
}

func (s *privacyStub) GetPrivacyProjection(_ context.Context, _ *coordinatorv1.GetPrivacyProjectionRequest, _ ...grpc.CallOption) (*coordinatorv1.PrivacyProjection, error) {
	return s.projectionResponse, nil
}

// TestGrpcClientCreateRunMapsPrivacyConfig is a regression test for the
// Go control plane's privacy-config gap: CreateRunRequest.PrivacyConfig
// exists on the generated wire struct but nothing ever set it, so a
// caller had no way to actually create a private run via the Go API.
func TestGrpcClientCreateRunMapsPrivacyConfig(t *testing.T) {
	stub := &privacyStub{}
	client := &GrpcClient{stub: stub}

	request := CreateRunRequest{
		RunID: "run-hybrid",
		Privacy: PrivacyConfig{
			Mode: PrivacyModeHybrid,
			SampleLevel: SampleLevelDPConfig{
				NoiseMultiplier: 0.9,
				MaxGradNorm:     1.2,
				TargetDelta:     1e-6,
				Accountant:      "prv",
				PoissonSampling: true,
				EpsilonBudget:   10,
			},
			UserLevel: UserLevelDPConfig{
				NoiseMultiplier:      1.0,
				TargetDelta:          1e-5,
				InitialClippingBound: 5.0,
				WeightingStrategy:    "uniform",
				EpsilonBudget:        50,
			},
			AdaptiveClipping: AdaptiveClippingConfig{
				Enabled:        true,
				TargetQuantile: 0.5,
				InitialClip:    1.0,
			},
			WarningThresholdFraction: 0.8,
		},
	}

	if _, err := client.CreateRun(context.Background(), request); err != nil {
		t.Fatalf("CreateRun: unexpected error: %v", err)
	}

	wire := stub.lastCreateRunRequest.GetPrivacyConfig()
	if wire == nil {
		t.Fatal("CreateRun: privacy_config never reached the wire request")
	}
	if wire.GetMode() != privacyv1.PrivacyMode_PRIVACY_MODE_HYBRID_DP {
		t.Errorf("Mode = %v, want PRIVACY_MODE_HYBRID_DP", wire.GetMode())
	}
	if wire.GetSampleLevel().GetNoiseMultiplier() != 0.9 {
		t.Errorf("SampleLevel.NoiseMultiplier = %v, want 0.9", wire.GetSampleLevel().GetNoiseMultiplier())
	}
	if wire.GetSampleLevel().GetAccountant() != privacyv1.AccountantType_ACCOUNTANT_TYPE_PRV {
		t.Errorf("SampleLevel.Accountant = %v, want ACCOUNTANT_TYPE_PRV", wire.GetSampleLevel().GetAccountant())
	}
	if wire.GetUserLevel().GetInitialClippingBound() != 5.0 {
		t.Errorf("UserLevel.InitialClippingBound = %v, want 5.0", wire.GetUserLevel().GetInitialClippingBound())
	}
	if !wire.GetAdaptiveClipping().GetEnabled() {
		t.Error("AdaptiveClipping.Enabled not mapped")
	}
	if wire.GetWarningThresholdFraction() != 0.8 {
		t.Errorf("WarningThresholdFraction = %v, want 0.8", wire.GetWarningThresholdFraction())
	}
}

// TestGrpcClientCreateRunDefaultsToUnspecifiedPrivacyMode confirms a
// zero-value CreateRunRequest.Privacy (the common, non-private case)
// renders as PRIVACY_MODE_UNSPECIFIED, matching pre-privacy-config
// behavior on the coordinator side (config_from_request treats
// UNSPECIFIED identically to NONE).
func TestGrpcClientCreateRunDefaultsToUnspecifiedPrivacyMode(t *testing.T) {
	stub := &privacyStub{}
	client := &GrpcClient{stub: stub}

	if _, err := client.CreateRun(context.Background(), CreateRunRequest{RunID: "run-plain"}); err != nil {
		t.Fatalf("CreateRun: unexpected error: %v", err)
	}
	wire := stub.lastCreateRunRequest.GetPrivacyConfig()
	if wire.GetMode() != privacyv1.PrivacyMode_PRIVACY_MODE_UNSPECIFIED {
		t.Errorf("Mode = %v, want PRIVACY_MODE_UNSPECIFIED for a zero-value Privacy field", wire.GetMode())
	}
}

func TestGrpcClientGetPrivacyMetricsMapsSeparateEpsilons(t *testing.T) {
	stub := &privacyStub{
		metricsResponse: &privacyv1.PrivacyMetricsSnapshot{
			RunId:          "run-hybrid",
			RoundId:        3,
			HasSampleLevel: true,
			SampleEpsilon:  1.5,
			SampleDelta:    1e-6,
			HasUserLevel:   true,
			UserEpsilon:    4.2,
			UserDelta:      1e-5,
		},
	}
	client := &GrpcClient{stub: stub}

	snapshot, err := client.GetPrivacyMetrics(context.Background(), "run-hybrid")
	if err != nil {
		t.Fatalf("GetPrivacyMetrics: unexpected error: %v", err)
	}
	if snapshot.SampleEpsilon != 1.5 || snapshot.UserEpsilon != 4.2 {
		t.Errorf("epsilons not mapped correctly: sample=%v user=%v", snapshot.SampleEpsilon, snapshot.UserEpsilon)
	}
	// Critical Privacy Rule regression guard: these must never be equal
	// by construction here (independent fields, independently set) —
	// this test would also catch an accidental aliasing bug where both
	// fields read from the same wire getter.
	if snapshot.SampleEpsilon == snapshot.UserEpsilon {
		t.Fatal("sample-level and user-level epsilon must never be combined/aliased")
	}
	if snapshot.HasClipping || snapshot.ClippingEpsilon != 0 {
		t.Errorf("clipping should be unset for this run: has=%v epsilon=%v", snapshot.HasClipping, snapshot.ClippingEpsilon)
	}
}

func TestGrpcClientGetPrivacyLedgerMapsAllThreeLists(t *testing.T) {
	stub := &privacyStub{
		ledgerResponse: &coordinatorv1.GetPrivacyLedgerResponse{
			SampleLevelEntries: []*privacyv1.SampleLevelLedgerEntry{
				{RunId: "run-1", ClientId: "client-a", Epsilon: 1.1},
			},
			UserLevelEntries: []*privacyv1.UserLevelLedgerEntry{
				{RunId: "run-1", RoundId: 1, Epsilon: 2.2, NumClients: 3},
			},
			ClippingEntries: []*privacyv1.AdaptiveClippingLedgerEntry{
				{RunId: "run-1", RoundId: 1, ClipValue: 0.8},
			},
			NextPageToken: "5",
		},
	}
	client := &GrpcClient{stub: stub}

	ledger, err := client.GetPrivacyLedger(context.Background(), "run-1", "", 0)
	if err != nil {
		t.Fatalf("GetPrivacyLedger: unexpected error: %v", err)
	}
	if len(ledger.SampleLevelEntries) != 1 || ledger.SampleLevelEntries[0].ClientID != "client-a" {
		t.Errorf("SampleLevelEntries not mapped: %+v", ledger.SampleLevelEntries)
	}
	if len(ledger.UserLevelEntries) != 1 || ledger.UserLevelEntries[0].NumClients != 3 {
		t.Errorf("UserLevelEntries not mapped: %+v", ledger.UserLevelEntries)
	}
	if len(ledger.ClippingEntries) != 1 || ledger.ClippingEntries[0].ClipValue != 0.8 {
		t.Errorf("ClippingEntries not mapped: %+v", ledger.ClippingEntries)
	}
	if ledger.NextPageToken != "5" {
		t.Errorf("NextPageToken = %q, want %q", ledger.NextPageToken, "5")
	}
}

// TestGrpcClientGetPrivacyProjectionTranslatesInfiniteBudgetToNil is a
// regression test for a real crash risk: the C++ coordinator reports an
// unset epsilon_budget as +Inf (a valid protobuf double), but Go's
// encoding/json refuses to marshal +Inf/NaN at all — silently carrying
// +Inf through to the HTTP layer would make every response containing
// it fail to serialize. budgetRemainingPointer must convert +Inf to nil.
func TestGrpcClientGetPrivacyProjectionTranslatesInfiniteBudgetToNil(t *testing.T) {
	stub := &privacyStub{
		projectionResponse: &coordinatorv1.PrivacyProjection{
			HasUserLevel:            true,
			UserCurrentEpsilon:      3.0,
			UserBudgetRemaining:     math.Inf(1),
			HasClipping:             true,
			ClippingCurrentEpsilon:  0.5,
			ClippingBudgetRemaining: 12.5,
		},
	}
	client := &GrpcClient{stub: stub}

	projection, err := client.GetPrivacyProjection(context.Background(), "run-1")
	if err != nil {
		t.Fatalf("GetPrivacyProjection: unexpected error: %v", err)
	}
	if projection.UserBudgetRemaining != nil {
		t.Errorf("UserBudgetRemaining = %v, want nil (unset epsilon_budget)", *projection.UserBudgetRemaining)
	}
	if projection.ClippingBudgetRemaining == nil || *projection.ClippingBudgetRemaining != 12.5 {
		t.Errorf("ClippingBudgetRemaining not mapped correctly: %v", projection.ClippingBudgetRemaining)
	}
}

func TestGrpcClientListWorkersMapsPrivacyCapabilities(t *testing.T) {
	stub := &privacyStub{
		listWorkersResponse: &coordinatorv1.ListWorkersResponse{
			Workers: []*coordinatorv1.WorkerSummary{
				{
					WorkerId: "worker-a",
					Status:   workerv1.WorkerStatus_WORKER_STATUS_IDLE,
					Capability: &workerv1.WorkerCapability{
						Device: "cpu",
						Privacy: &privacyv1.WorkerPrivacyCapabilities{
							SupportsSampleLevelDp: true,
							OpacusVersion:         "1.6.0",
							SupportedAccountants:  []privacyv1.AccountantType{privacyv1.AccountantType_ACCOUNTANT_TYPE_RDP},
						},
					},
					RegisteredAtUnixS: 100.0,
				},
			},
		},
	}
	client := &GrpcClient{stub: stub}

	workers, err := client.ListWorkers(context.Background())
	if err != nil {
		t.Fatalf("ListWorkers: unexpected error: %v", err)
	}
	if len(workers) != 1 {
		t.Fatalf("ListWorkers returned %d workers, want 1", len(workers))
	}
	worker := workers[0]
	if worker.WorkerID != "worker-a" || worker.Status != "IDLE" {
		t.Errorf("worker identity/status not mapped: %+v", worker)
	}
	if !worker.Privacy.SupportsSampleLevelDP || worker.Privacy.OpacusVersion != "1.6.0" {
		t.Errorf("privacy capabilities not mapped: %+v", worker.Privacy)
	}
	if len(worker.Privacy.SupportedAccountants) != 1 || worker.Privacy.SupportedAccountants[0] != "rdp" {
		t.Errorf("SupportedAccountants not mapped: %+v", worker.Privacy.SupportedAccountants)
	}
}

func TestMockClientListWorkers(t *testing.T) {
	client := NewMockClient()
	ctx := context.Background()

	if workers, err := client.ListWorkers(ctx); err != nil || len(workers) != 0 {
		t.Fatalf("ListWorkers before seeding = %v, %v; want empty, nil error", workers, err)
	}

	client.SeedWorker(WorkerSummary{
		WorkerID: "worker-a",
		Status:   "IDLE",
		Privacy:  WorkerPrivacyCapabilities{SupportsSampleLevelDP: true, OpacusVersion: "1.6.0"},
	})
	client.SeedWorker(WorkerSummary{WorkerID: "worker-b", Status: "IDLE"})

	workers, err := client.ListWorkers(ctx)
	if err != nil {
		t.Fatalf("ListWorkers: unexpected error: %v", err)
	}
	if len(workers) != 2 {
		t.Fatalf("ListWorkers returned %d workers, want 2", len(workers))
	}
	if workers[0].WorkerID != "worker-a" || !workers[0].Privacy.SupportsSampleLevelDP {
		t.Errorf("first seeded worker not returned correctly: %+v", workers[0])
	}
	if workers[1].WorkerID != "worker-b" || workers[1].Privacy.SupportsSampleLevelDP {
		t.Errorf("second seeded worker not returned correctly: %+v", workers[1])
	}
}

func TestMockClientPrivacyRoundTrip(t *testing.T) {
	client := NewMockClient()
	ctx := context.Background()

	if _, err := client.CreateRun(ctx, CreateRunRequest{
		RunID:   "run-1",
		Privacy: PrivacyConfig{Mode: PrivacyModeUserLevel},
	}); err != nil {
		t.Fatalf("CreateRun: unexpected error: %v", err)
	}
	if got := client.PrivacyConfigFor("run-1").Mode; got != PrivacyModeUserLevel {
		t.Errorf("PrivacyConfigFor returned Mode=%q, want %q", got, PrivacyModeUserLevel)
	}

	// Before seeding, a real (non-private-looking) response is still
	// returned, not an error — Critical Privacy Rule's flip side.
	metrics, err := client.GetPrivacyMetrics(ctx, "run-1")
	if err != nil {
		t.Fatalf("GetPrivacyMetrics before seeding: unexpected error: %v", err)
	}
	if metrics.HasUserLevel {
		t.Error("unseeded run should report HasUserLevel=false")
	}

	client.SeedPrivacyMetrics("run-1", PrivacyMetricsSnapshot{HasUserLevel: true, UserEpsilon: 2.5})
	metrics, err = client.GetPrivacyMetrics(ctx, "run-1")
	if err != nil {
		t.Fatalf("GetPrivacyMetrics after seeding: unexpected error: %v", err)
	}
	if !metrics.HasUserLevel || metrics.UserEpsilon != 2.5 {
		t.Errorf("seeded metrics not returned: %+v", metrics)
	}

	if _, err := client.GetPrivacyMetrics(ctx, "run-does-not-exist"); err != ErrRunNotFound {
		t.Errorf("GetPrivacyMetrics for unknown run = %v, want ErrRunNotFound", err)
	}
}
