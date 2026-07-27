package coordinator

import (
	"context"
	"fmt"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	coordinatorv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/coordinator/v1"
	experimentv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/experiment/v1"
	workerv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/worker/v1"
)

// modelManifestToWire converts a ModelManifest into its wire
// representation. Declared standalone (not inlined into CreateRun) so the
// CreateRun mapping test (grpc_client_test.go) can assert on its output
// directly.
func modelManifestToWire(manifest ModelManifest) *coordinatorv1.ModelManifest {
	tensors := make([]*workerv1.TensorManifest, 0, len(manifest.Tensors))
	for _, tensor := range manifest.Tensors {
		tensors = append(tensors, &workerv1.TensorManifest{
			Name:  tensor.Name,
			Shape: tensor.Shape,
			// Only float32 is a supported domain dtype today (see
			// fl_core/tensor.hpp's DType enum) — the coordinator does not
			// currently branch on this field, but it is set explicitly
			// rather than left as an empty/ambiguous default.
			Dtype: "float32",
		})
	}
	return &coordinatorv1.ModelManifest{
		ModelId:      manifest.ModelID,
		ModelVersion: manifest.ModelVersion,
		Tensors:      tensors,
		AggregationManifest: &coordinatorv1.AggregationManifest{
			SharedParameterNames:       manifest.AggregationManifest.SharedParameterNames,
			PersonalizedParameterNames: manifest.AggregationManifest.PersonalizedParameterNames,
			FrozenParameterNames:       manifest.AggregationManifest.FrozenParameterNames,
			SchemaHash:                 manifest.AggregationManifest.SchemaHash,
		},
	}
}

// pollEventsWindow bounds a single PollEvents call. The coordinator's
// StreamRunEvents RPC is a genuinely long-lived stream (it loops on the
// server side until the client cancels — see
// CoordinatorServiceImpl::StreamRunEvents), so without an internal
// deadline here, PollEvents would block on stream.Recv() until an event
// arrives rather than returning what's already available — which starves
// callers built around "poll every N seconds and forward what came back"
// (see httpapi.handleCoordinatorRunEvents). Discovered by actually
// running the coordinator+api containers together in docker-compose;
// see docs/event-streaming.md.
const pollEventsWindow = 8 * time.Second

// GrpcClient is a real gRPC client against the coordinator's
// CoordinatorService. The insecure path has not been exercised against
// a live C++ coordinator server in this environment — no local gRPC C++
// toolchain is available here (see docs/coordinator-runtime.md) — but
// the Go gRPC/protobuf stack itself is pure Go (no cgo), so this code
// compiles and its request/response mapping is real, not a stub. The
// TLS/mTLS path (this file's transport.go) has been exercised against a
// real local TLS listener using this project's own development PKI —
// see go/internal/coordinator/transport_test.go and
// docs/transport-identity-validation.md. Application code depends on
// the Client interface, not this type, specifically so MockClient can
// stand in wherever a live coordinator isn't available (as it does in
// this repository's own Go tests).
type GrpcClient struct {
	config        Config
	transportMode TransportMode
	conn          *grpc.ClientConn
	stub          coordinatorv1.CoordinatorServiceClient
}

// TransportMode reports how this client is actually connected — safe to
// expose through the Go security API (Work Package O) and audit
// metadata (Work Package F).
func (c *GrpcClient) TransportMode() TransportMode {
	return c.transportMode
}

func NewGrpcClient(config Config) (*GrpcClient, error) {
	var dialOptions []grpc.DialOption
	var transportMode TransportMode
	if config.Insecure {
		// Never the silent default — Config.Insecure must be explicitly
		// true, which DefaultConfig sets visibly (see its doc comment),
		// matching the closure-gate requirement that insecure transport
		// requires an explicit opt-in, never an implicit fallback from a
		// missing/empty TLS config.
		dialOptions = append(dialOptions, grpc.WithTransportCredentials(insecure.NewCredentials()))
		transportMode = TransportModeInsecureDevelopment
	} else {
		if config.TLS == nil {
			return nil, fmt.Errorf("%w: Config.Insecure is false but Config.TLS is nil; TLS/mTLS requires a populated TLSConfig, and insecure transport requires Insecure: true explicitly — see docs/mtls.md", ErrUnavailable)
		}
		creds, mode, err := buildTransportCredentials(*config.TLS)
		if err != nil {
			return nil, err
		}
		dialOptions = append(dialOptions, grpc.WithTransportCredentials(creds))
		transportMode = mode
	}

	conn, err := grpc.NewClient(config.Address, dialOptions...)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	return &GrpcClient{
		config:        config,
		transportMode: transportMode,
		conn:          conn,
		stub:          coordinatorv1.NewCoordinatorServiceClient(conn),
	}, nil
}

func (c *GrpcClient) Close() error {
	return c.conn.Close()
}

func (c *GrpcClient) Health(ctx context.Context) (string, error) {
	response, err := c.stub.Health(ctx, &coordinatorv1.HealthRequest{})
	if err != nil {
		return "", mapGrpcError(err)
	}
	return response.GetStatus(), nil
}

func (c *GrpcClient) CreateRun(ctx context.Context, request CreateRunRequest) (RunSnapshot, error) {
	response, err := c.stub.CreateRun(ctx, &coordinatorv1.CreateRunRequest{
		Config: &experimentv1.RunConfiguration{
			RunId:     request.RunID,
			Algorithm: &experimentv1.AlgorithmConfig{Name: request.Algorithm},
			Rounds:    request.MaxRounds,
		},
		Optimizer: &coordinatorv1.OptimizerConfig{
			Algorithm: request.Algorithm,
			Weighting: request.Weighting,
			ServerLr:  request.ServerLR,
		},
		TargetClientsPerRound: request.TargetClientsPerRound,
		TotalClients:          request.TotalClients,
		MaxRounds:             request.MaxRounds,
		RoundTimeoutSeconds:   request.RoundTimeoutSeconds,
		MinimumValidResults:   request.MinimumValidResults,
		ClientSelectionSeed:   request.ClientSelectionSeed,
		ClientIds:             request.ClientIDs,
		LocalEpochs:           request.LocalEpochs,
		BatchSize:             request.BatchSize,
		LearningRate:          request.LearningRate,
		Momentum:              request.Momentum,
		WeightDecay:           request.WeightDecay,
		FedproxMu:             request.FedProxMu,
		TaskLeaseSeconds:      request.TaskLeaseSeconds,
		MaxTaskRetries:        request.MaxTaskRetries,
		ModelManifest:         modelManifestToWire(request.ModelManifest),
		RequestId:             request.RequestID,
		PrivacyConfig:         privacyConfigToWire(request.Privacy),
	})
	if err != nil {
		return RunSnapshot{}, mapGrpcError(err)
	}
	return toRunSnapshot(response.GetState(), response.GetRunId(), 0, request.MaxRounds, "v0", request.Algorithm, 0, 0), nil
}

func (c *GrpcClient) StartRun(ctx context.Context, runID, traceID string) (RunSnapshot, error) {
	response, err := c.stub.StartRun(ctx, &coordinatorv1.StartRunRequest{RunId: runID, TraceId: traceID})
	if err != nil {
		return RunSnapshot{}, mapGrpcError(err)
	}
	return toRunSnapshot(response.GetState(), response.GetRunId(), response.GetCurrentRound(), 0, response.GetModelVersion(), "", 0, 0), nil
}

func (c *GrpcClient) PauseRun(ctx context.Context, runID, reason, traceID string) (RunSnapshot, error) {
	response, err := c.stub.PauseRun(ctx, &coordinatorv1.PauseRunRequest{RunId: runID, Reason: reason, TraceId: traceID})
	if err != nil {
		return RunSnapshot{}, mapGrpcError(err)
	}
	return toRunSnapshot(response.GetState(), response.GetRunId(), response.GetCurrentRound(), 0, response.GetModelVersion(), "", 0, 0), nil
}

func (c *GrpcClient) ResumeRun(ctx context.Context, runID, traceID string) (RunSnapshot, error) {
	response, err := c.stub.ResumeRun(ctx, &coordinatorv1.ResumeRunRequest{RunId: runID, TraceId: traceID})
	if err != nil {
		return RunSnapshot{}, mapGrpcError(err)
	}
	return toRunSnapshot(response.GetState(), response.GetRunId(), response.GetCurrentRound(), 0, response.GetModelVersion(), "", 0, 0), nil
}

func (c *GrpcClient) CancelRun(ctx context.Context, runID, reason, traceID string) (RunSnapshot, error) {
	response, err := c.stub.CancelRun(ctx, &coordinatorv1.CancelRunRequest{RunId: runID, Reason: reason, TraceId: traceID})
	if err != nil {
		return RunSnapshot{}, mapGrpcError(err)
	}
	return toRunSnapshot(response.GetState(), response.GetRunId(), response.GetCurrentRound(), 0, response.GetModelVersion(), "", 0, 0), nil
}

func (c *GrpcClient) GetRun(ctx context.Context, runID string) (RunSnapshot, error) {
	response, err := c.stub.GetRun(ctx, &coordinatorv1.GetRunRequest{RunId: runID})
	if err != nil {
		return RunSnapshot{}, mapGrpcError(err)
	}
	return toRunSnapshot(
		response.GetState(), response.GetRunId(), response.GetCurrentRound(), response.GetMaxRounds(),
		response.GetModelVersion(), response.GetAlgorithm(), response.GetRegisteredWorkers(), response.GetHealthyWorkers(),
	), nil
}

func (c *GrpcClient) GetPersonalizationSummary(ctx context.Context, runID string) ([]PersonalizationMetricRecord, error) {
	response, err := c.stub.GetPersonalizationSummary(ctx, &coordinatorv1.GetPersonalizationSummaryRequest{RunId: runID})
	if err != nil {
		return nil, mapGrpcError(err)
	}
	records := make([]PersonalizationMetricRecord, 0, len(response.GetRecords()))
	for _, wireRecord := range response.GetRecords() {
		records = append(records, wirePersonalizationRecordToRecord(wireRecord))
	}
	return records, nil
}

func (c *GrpcClient) GetPrivacyMetrics(ctx context.Context, runID string) (PrivacyMetricsSnapshot, error) {
	response, err := c.stub.GetPrivacyMetrics(ctx, &coordinatorv1.GetPrivacyMetricsRequest{RunId: runID})
	if err != nil {
		return PrivacyMetricsSnapshot{}, mapGrpcError(err)
	}
	return wirePrivacyMetricsToSnapshot(response), nil
}

func (c *GrpcClient) GetPrivacyLedger(ctx context.Context, runID, pageToken string, pageSize uint32) (PrivacyLedger, error) {
	response, err := c.stub.GetPrivacyLedger(ctx, &coordinatorv1.GetPrivacyLedgerRequest{
		RunId:     runID,
		PageToken: pageToken,
		PageSize:  pageSize,
	})
	if err != nil {
		return PrivacyLedger{}, mapGrpcError(err)
	}
	ledger := PrivacyLedger{
		SampleLevelEntries: make([]SampleLevelLedgerEntry, 0, len(response.GetSampleLevelEntries())),
		UserLevelEntries:   make([]UserLevelLedgerEntry, 0, len(response.GetUserLevelEntries())),
		ClippingEntries:    make([]AdaptiveClippingLedgerEntry, 0, len(response.GetClippingEntries())),
		NextPageToken:      response.GetNextPageToken(),
	}
	for _, wireEntry := range response.GetSampleLevelEntries() {
		ledger.SampleLevelEntries = append(ledger.SampleLevelEntries, wireSampleLevelEntryToEntry(wireEntry))
	}
	for _, wireEntry := range response.GetUserLevelEntries() {
		ledger.UserLevelEntries = append(ledger.UserLevelEntries, wireUserLevelEntryToEntry(wireEntry))
	}
	for _, wireEntry := range response.GetClippingEntries() {
		ledger.ClippingEntries = append(ledger.ClippingEntries, wireClippingEntryToEntry(wireEntry))
	}
	return ledger, nil
}

func (c *GrpcClient) GetPrivacyProjection(ctx context.Context, runID string) (PrivacyProjection, error) {
	response, err := c.stub.GetPrivacyProjection(ctx, &coordinatorv1.GetPrivacyProjectionRequest{RunId: runID})
	if err != nil {
		return PrivacyProjection{}, mapGrpcError(err)
	}
	return wirePrivacyProjectionToProjection(response), nil
}

func (c *GrpcClient) ListWorkers(ctx context.Context) ([]WorkerSummary, error) {
	response, err := c.stub.ListWorkers(ctx, &coordinatorv1.ListWorkersRequest{})
	if err != nil {
		return nil, mapGrpcError(err)
	}
	summaries := make([]WorkerSummary, 0, len(response.GetWorkers()))
	for _, wireSummary := range response.GetWorkers() {
		summaries = append(summaries, wireWorkerSummaryToSummary(wireSummary))
	}
	return summaries, nil
}

func (c *GrpcClient) PollEvents(ctx context.Context, runID, afterEventID string) ([]Event, error) {
	started := time.Now()
	pollCtx, cancel := context.WithTimeout(ctx, pollEventsWindow)
	defer cancel()
	stream, err := c.stub.StreamRunEvents(pollCtx, &coordinatorv1.StreamRunEventsRequest{
		RunId:              runID,
		ResumeAfterEventId: afterEventID,
	})
	// windowElapsed is checked both here and in the Recv() loop below.
	// The coordinator's StreamRunEvents loops forever until the client's
	// deadline lapses (see coordinator_service.cpp / main.cpp), so hitting
	// our own pollEventsWindow is the *normal*, expected way this call
	// ends on every poll that finds nothing new — not a transport
	// failure. That can surface as an error from either the initial
	// stub.StreamRunEvents call (observed here to be where the client-side
	// gRPC stream is actually established, contrary to the "streaming
	// calls never block" assumption — it blocked for the full window when
	// run against the coordinator over the docker-compose bridge network,
	// though not when dialed via the coordinator's host-published port;
	// root cause not pinned down further) or from stream.Recv(). Detecting
	// it by elapsed wall-clock time against the window we ourselves set —
	// rather than pollCtx.Err() or a specific grpc status code — is
	// deliberate: both were observed to be unreliable signals here (a
	// pollCtx.Err()-vs-Recv()-error race, and a status code/message that
	// varied between codes.Unavailable/"context deadline exceeded" and
	// codes.DeadlineExceeded/"Deadline Exceeded" across otherwise-identical
	// calls). See docs/event-streaming.md.
	windowElapsed := func() bool { return time.Since(started) >= pollEventsWindow-50*time.Millisecond }
	if err != nil {
		if windowElapsed() {
			return nil, nil
		}
		return nil, mapGrpcError(err)
	}
	var events []Event
	for {
		wireEvent, recvErr := stream.Recv()
		if recvErr != nil {
			if windowElapsed() {
				return events, nil
			}
			return events, mapGrpcError(recvErr)
		}
		events = append(events, wireEventToEvent(wireEvent))
	}
}
