# Security Operations Grafana Dashboard

**Status: Implemented and live-validated.** Security Runtime Completion
and Release Evidence slice, Work Package R. Grafana was already part of
the active Compose stack (`infra/compose/docker-compose.dev.yml`,
provisioned with a `Prometheus` datasource in
`infra/grafana/datasources.yml`), but shipped zero dashboards and had
no dashboard-provisioning wiring at all before this slice — this is the
first dashboard this repository ships.

## What was added

- `infra/grafana/datasources.yml`: gave the existing `Prometheus`
  datasource a fixed `uid: prometheus_ds` so the dashboard below can
  reference it by a stable id instead of a name lookup.
- `infra/grafana/dashboard-provisioning.yml`: a file-based dashboard
  provider pointing at `/etc/grafana/provisioning/dashboards/security`.
- `infra/grafana/dashboards/security.json`: the dashboard itself,
  `uid: fl-security-operations`, 6 panels (see below).
- `infra/compose/docker-compose.dev.yml`'s `grafana` service: two new
  read-only volume mounts wiring the provisioning file and the
  dashboards directory into the container.

## Panels

All six panels are built from the low-cardinality metrics documented in
[security-metrics.md](security-metrics.md) and
[security-event-source-health.md](security-event-source-health.md) —
no panel queries a per-worker or per-task label, matching this
project's explicit "no per-worker metric label" discipline.

| Panel | Query | Purpose |
|---|---|---|
| Security events rate by severity | `sum by (severity) (rate(fl_security_events_total[5m]))` | The primary alerting signal: a sustained rise in CRITICAL/HIGH. |
| Security events rate by category and outcome | `sum by (category, outcome) (rate(fl_security_events_total[5m]))` | Which coarse category (transport/worker_identity/worker_signing_key/signed_message/coordinator_task/administration) is producing rejections. |
| Event source lag (seconds) | `fl_security_event_source_lag_seconds` | Per source_service (go-api/coordinator/python-worker); the fixed 120s staleness threshold is marked as a red field threshold. |
| Event source record counts | `fl_security_event_source_records` | Per-source journal size over time. |
| Worker security-event batch outcomes | `fl_security_event_source_batches` (by `outcome`) | A sustained non-zero `rejected` series means a worker's batches are failing coordinator-side verification. |
| Distinct workers seen (coordinator source) | `fl_security_event_source_distinct_workers{source_service="coordinator"}` | An aggregate count only — never a worker-ID list. |

## What this dashboard does not chart

Secure aggregation, pairwise masking, secret sharing, or any other item
under this project's "Explicitly Out of Scope" list — none of it is
implemented, so there is nothing real to chart. See
[known-limitations.md](known-limitations.md) and
[security-events.md](security-events.md)'s `feature_availability`
contract for the live, queryable version of the same disclosure.

## Validation

Live-checked this slice: `docker compose up -d grafana prometheus`
against the updated compose file, then
`GET http://localhost:3001/api/dashboards/uid/fl-security-operations`
(basic-auth `admin`/`admin`, the Grafana image's default dev
credentials) returned the real, fully provisioned dashboard JSON with
`"provisionedExternalId":"security.json"` and all 6 panels present —
not merely that the container started. Grafana's own log confirmed
`"finished to provision dashboards"` with no error in between.
