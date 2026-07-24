# Go Coordinator Integration

## Layering

```
HTTP handler (httpapi) → application service (application.CoordinatorService) → coordinator.Client interface → gRPC implementation
```

HTTP handlers never call gRPC directly. `go/internal/coordinator/`:

* `client.go` — the `Client` interface (`Health`, `CreateRun`, `StartRun`,
  `PauseRun`, `ResumeRun`, `CancelRun`, `GetRun`, `PollEvents`), plus
  `RunSnapshot`/`CreateRunRequest`/`Event`/`Config`.
* `grpc_client.go` — `GrpcClient`, a real implementation over the
  generated `coordinatorv1` package. Exercised end-to-end against a live
  coordinator container this milestone (see
  [milestone-3-validation.md](milestone-3-validation.md)).
* `mock_client.go` — `MockClient`, an in-memory implementation
  replicating the C++ coordinator's idempotency rules (start/pause/
  resume/cancel are idempotent when already in the target state, reject
  invalid transitions). Used by this repo's own Go tests — no live
  coordinator needed to test the HTTP↔application layers.
* `mapper.go` — `mapGrpcError` (gRPC status → `ErrUnavailable`/
  `ErrRunNotFound`/`RejectedError`), plus wire↔domain type conversions.
* `errors.go` — `ErrUnavailable`, `ErrRejected`, `ErrRunNotFound`,
  `RejectedError`.

## Why coordinator routes are separate from `/api/v1/runs`

Milestone 1 already defined `/api/v1/runs` for local project/experiment/
run *bookkeeping* (a `runs.Run` record backed by a file/in-memory
repository — status, config, timestamps). That is a different concept
from a live federated round being driven by the C++ coordinator. Rather
than overloading the same path (which would have required either
breaking M1's tested behavior or an invasive merge under time pressure),
Milestone 3's coordinator-backed routes live under a new prefix:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/coordinator/runs` | Create a coordinator run |
| POST | `/api/v1/coordinator/runs/{runId}/start` | |
| POST | `/api/v1/coordinator/runs/{runId}/pause` | body: `{"reason": "..."}` |
| POST | `/api/v1/coordinator/runs/{runId}/resume` | |
| POST | `/api/v1/coordinator/runs/{runId}/cancel` | body: `{"reason": "..."}` |
| GET | `/api/v1/coordinator/runs/{runId}` | Full `RunSnapshot` |
| GET | `/api/v1/coordinator/runs/{runId}/rounds/current` | Round/model-version projection |
| GET | `/api/v1/coordinator/runs/{runId}/metrics` | Round/worker-count projection (no fabricated accuracy/loss) |
| GET | `/api/v1/coordinator/runs/{runId}/events` | SSE event stream |
| GET | `/api/v1/system/coordinator-health` | Coordinator `Health()` passthrough |
| GET | `/metrics` | Prometheus exposition (unauthenticated, like `/healthz`) |

This is a disclosed deviation from the literal endpoint list in the
original task spec (which assumed `/api/v1/runs` was greenfield) — see
[milestone-3-report.md](milestone-3-report.md).

## Error mapping

`writeCoordinatorError` (`go/internal/transport/httpapi/coordinator_handlers.go`):

| Coordinator error | HTTP status |
|---|---|
| `application.ErrCoordinatorNotConfigured` | 503 |
| `coordinator.ErrUnavailable` | 503 |
| `coordinator.ErrRunNotFound` | 404 |
| `coordinator.ErrRejected` | 409 |
| anything else | 500 |

`Services.Coordinator` is optional — `coordinatorClient` may be `nil`
(local dev without a running coordinator process), in which case every
`CoordinatorService` method returns `ErrCoordinatorNotConfigured` rather
than panicking. Wired via `FL_COORDINATOR_ADDRESS` in `cmd/api/main.go`;
unset, the API starts fine and coordinator routes 503.

## Events (SSE)

See [event-streaming.md](event-streaming.md).

## Testing

`go/internal/coordinator` (11 tests: `MockClient` lifecycle/idempotency,
`mapGrpcError` status-code mapping) and
`go/internal/transport/httpapi/coordinator_handlers_test.go` (8 tests:
health unconfigured/configured, create/lifecycle/get/metrics/events,
403 on insufficient role, 409 on duplicate, 503 unconfigured) all use
`MockClient` — no live coordinator needed. `go test -race` is CI-only
(no cgo locally, same as Milestones 1–2).
