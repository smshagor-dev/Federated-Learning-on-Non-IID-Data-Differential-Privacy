package coordinator

import (
	"context"
	"fmt"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	coordinatorv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/coordinator/v1"
	experimentv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/experiment/v1"
)

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
// CoordinatorService. It has not been exercised against a live C++
// coordinator server in this environment — no local gRPC C++ toolchain
// is available here (see docs/coordinator-runtime.md) — but the Go
// gRPC/protobuf stack itself is pure Go (no cgo), so this code compiles
// and its request/response mapping is real, not a stub. Application code
// depends on the Client interface, not this type, specifically so
// MockClient can stand in wherever a live coordinator isn't available
// (as it does in this repository's own Go tests).
type GrpcClient struct {
	config Config
	conn   *grpc.ClientConn
	stub   coordinatorv1.CoordinatorServiceClient
}

func NewGrpcClient(config Config) (*GrpcClient, error) {
	var dialOptions []grpc.DialOption
	if config.Insecure {
		dialOptions = append(dialOptions, grpc.WithTransportCredentials(insecure.NewCredentials()))
	} else {
		return nil, fmt.Errorf("%w: TLS credentials are a configuration hook for now; see docs/coordinator-runtime.md", ErrUnavailable)
	}

	conn, err := grpc.NewClient(config.Address, dialOptions...)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	return &GrpcClient{
		config: config,
		conn:   conn,
		stub:   coordinatorv1.NewCoordinatorServiceClient(conn),
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
