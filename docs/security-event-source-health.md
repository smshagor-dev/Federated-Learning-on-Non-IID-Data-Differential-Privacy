# Security Event Source Health

**Status: Implemented and validated.** Security Runtime Completion and
Release Evidence slice, Work Package P. See
[security-event-centralization.md](security-event-centralization.md)
for the pipeline this monitors and
[security-runtime-validation.md](security-runtime-validation.md) for
how the claims below were checked.

## Endpoint

`GET /api/v1/security/events/sources` returns one entry per source
service:

```json
{
  "sources": [
    {
      "source_service": "go-api",
      "last_event_at": "2026-07-25T12:00:00Z",
      "lag_seconds": 4.0,
      "record_count": 128,
      "recovered_line_count": 0,
      "corrupted": false,
      "retention_active": false,
      "stale": false
    },
    {
      "source_service": "coordinator",
      "record_count": 340,
      "batches_accepted": 12,
      "batches_rejected": 0,
      "distinct_workers_seen": 1,
      "stale": false
    },
    {
      "source_service": "python-worker",
      "record_count": 340,
      "batches_accepted": 12,
      "batches_rejected": 0,
      "distinct_workers_seen": 1,
      "stale": false
    }
  ],
  "checked_at_unix_s": 1780000000
}
```

Three low-cardinality `source_service` values only —
`go-api` (the Go process's own local `SecurityEventJournal`),
`coordinator` (the C++ coordinator's central journal, queried via
`GetSecurityEventSourceHealth`), and `python-worker` (an aggregate of
every worker's centralized batches, relayed through the same RPC). Per
Work Package P's explicit instruction, there is **no per-worker
label** — `distinct_workers_seen` is a count, never a set of worker
IDs, in this response or in the Prometheus gauges it feeds
(`go/internal/observability/telemetry.go`'s
`RecordSecurityEventSourceHealth`).

`batches_accepted`/`batches_rejected`/`distinct_workers_seen` are
coordinator-side, centrally verified counts — never a worker's own
self-reported queue depth, which would not be trustworthy as a health
signal (a compromised or buggy worker could report anything about
itself). Individual worker queue depth is not exposed by this endpoint
at all; see `docs/worker-security-event-queue.md` for why it is
inherently worker-local.

## Staleness threshold (Work Package P: "define fixed thresholds")

`go/internal/transport/httpapi/security_overview.go`'s
`staleSecurityEventSourceThresholdSeconds = 120.0` — a single, fixed
constant applied identically to every source, not a per-source-type or
per-worker threshold. Chosen as roughly 4x the python-worker's default
security-event flush interval (15s —
`WorkerConfig.security_event_flush_interval_seconds`) plus the Go
overview's own 5s polling interval, so a single missed flush cycle or a
slow poll does not flip a healthy source to stale, while a source that
has genuinely stopped reporting is caught within about two flush
cycles.

`stale` is `true` only when a source **has previously reported at
least one event** (`last_event_at` is set) **and** its current lag
exceeds the threshold. A source with no `last_event_at` yet (e.g. a
freshly started stack where python-worker hasn't completed its first
flush cycle) is reported as not-stale — "never reported" and "reported,
then went quiet" are different, differently-alarming states, and
conflating them would make the web console's Event Source Health table
flag every freshly started environment as unhealthy.

The web Security Center (`security-overview-console.tsx`'s Event
source health table) renders `stale` as a `sec-warn` (amber) status
pill, distinct from `corrupted` (`sec-bad`/red, checked first) and
`active` (`sec-good`/green, the default when neither is true).

## What was deliberately not added

- **A new Prometheus gauge for staleness.** `stale` is a pure function
  of the already-exported `fl_security_event_source_lag_seconds` gauge
  and the fixed threshold above — an operator (or Grafana) can already
  alert on `fl_security_event_source_lag_seconds > 120` directly.
  Adding a second `..._stale` gauge would duplicate that signal for no
  new information, which Work Package Q's own "add only missing
  metrics" instruction rules out.
- **Per-source or per-worker configurable thresholds.** A single fixed
  constant, per Work Package P's explicit instruction, kept in one
  place (`security_overview.go`) rather than as an environment
  variable or per-deployment config surface this slice's scope does not
  call for.

## Validation

Live-checked by `scripts/security-validation/groups/event_centralization.py`'s
`event-centralization.source-health.reports-accepted-batch` scenario
(real accepted-batch counters reflected in this endpoint) and by
`go/internal/transport/httpapi/security_overview_test.go`'s
`TestSecurityEventSourcesMarksStaleSourceAfterThreshold` (a source
seeded with a ~10-minute-old `last_event_at` is reported stale; one
seeded ~1 second old, and the never-reported `go-api` source in a fresh
test server, are not).
