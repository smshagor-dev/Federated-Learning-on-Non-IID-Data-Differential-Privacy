# Experiment Registry Existing Infrastructure Audit

This audit documents the repository infrastructure that can be reused
for the research experiment registry and typed Go research APIs.

## Classification Legend

- `Reuse unchanged`
- `Reuse with adaptation`
- `Incompatible`
- `Missing`
- `Deferred`

## Audit Summary

| Area | Current implementation | Classification | Notes |
|---|---|---|---|
| Atomic JSON replacement | `go/internal/storage/jsonfile.go`, Python atomic `Path.replace()` patterns in worker journals | Reuse with adaptation | Existing helpers provide directory creation and temp-file replace, but the registry needs checksum files, compare-and-update semantics, and stricter overwrite detection. |
| File-backed repositories | `go/internal/{projects,experiments,runs,models,datasets}` | Reuse with adaptation | Good for bootstrap and path conventions, but current experiment/run stores are list-style whole-file snapshots with no optimistic concurrency or per-record durability. |
| JSONL durable journals | `go/internal/observability/security_event_journal.go`, `security_audit_journal.go`, `python/src/fl_platform/security/security_event_journal.py` | Reuse with adaptation | Strong precedent for append-safe JSONL, checksums, cursor pagination, and skip-and-recover corruption handling. Research journals can mirror this shape with research-specific schemas. |
| Corruption recovery policy | Security journals skip bad lines and count recovered lines | Reuse with adaptation | Useful for append-only journals. Immutable registry/state files need a stricter fail-closed policy that marks records corrupted instead of silently skipping required files. |
| Checksums | FNV-1a payload checksums in security journals; SHA-256 already used in Python research spec and partition manifests | Reuse with adaptation | Research registry should standardize on SHA-256 for immutable artifacts and artifact manifests, while preserving checksum verification patterns already used elsewhere. |
| Cursor pagination | Security journal list APIs and secure user-level privacy routes | Reuse with adaptation | Response shape and cursor behavior are reusable. Research event/metric lists should use typed cursors scoped to one journal. |
| RBAC permissions | `go/internal/security/permissions.go`, existing `AuthService.Authorize` | Reuse with adaptation | Permission-constant pattern is strong. Research APIs need a new permission set rather than inline role lists. |
| Role-specific serialization | `go/internal/transport/httpapi/security_handlers.go` | Reuse with adaptation | The security API already proves that role-aware projection should be explicit and type-specific instead of “serialize then delete fields.” |
| HTTP error mapping | `server.go`, `security_handlers.go`, dataset/model handlers | Reuse unchanged | Existing `writeError` conventions and service-to-HTTP mapping are appropriate for research endpoints. |
| Idempotency | `go/internal/transport/httpapi/security_handlers.go` in-memory cache; coordinator-side idempotent security mutations | Reuse with adaptation | The pattern is correct, but research experiment creation needs durable idempotency persisted with the registry, not process-memory-only caching. |
| Audit logging | `AuditService`, `observability.AuditRepository` | Reuse unchanged | Existing audit records can log high-level research mutations alongside the new research-specific event journal. |
| Runtime health | `/healthz`, security overview/source-health routes, secure user-level DP health | Reuse with adaptation | Health route shape and degraded-state conventions are reusable. Research APIs need typed store/orchestrator health rather than only coordinator security health. |
| Docker persistent volume conventions | `FL_CONTROL_PLANE_DATA_DIR`, `bootstrap.PathsForDataDir`, Compose data mounts | Reuse unchanged | Research storage should live under the same control-plane data root, not in source-controlled fixture paths. |
| Artifact sanitation | `scripts/security-validation/check_artifact_sanitation.py` | Reuse with adaptation | Existing deny-list scanning is a good start. Research artifacts need additional prohibited patterns for dataset samples, clear updates, norms, indicators, and secrets. |
| Checkpoint persistence | C++ coordinator checkpoint store, Python worker task journal | Reuse with adaptation | Recovery semantics are informative, but research checkpoints need metadata-only registration, not raw model payload duplication in the registry layer. |
| Locking / concurrency | Mutexes inside repositories and journals | Reuse with adaptation | In-process locking exists, but the registry needs explicit version checks and durable compare-and-update behavior for restart safety. |
| Safe path joining | Ad hoc `filepath.Join`, path-root conventions | Reuse with adaptation | The registry should add explicit safe-ID and relative-path validation helpers instead of trusting arbitrary request strings. |
| Environment manifest collection | No unified research environment manifest yet | Missing | Must be implemented. Existing version-reporting/security health surfaces can inform its fields. |
| Research artifact manifest | No unified artifact manifest yet | Missing | Must be implemented. |
| Restart recovery scan | Worker accepted-task recovery, security journal reload | Reuse with adaptation | There is a clear precedent for startup recovery scans. Research startup should scan experiments/runs and classify stale active runs. |
| Multi-seed orchestration | No bounded research orchestrator yet | Missing | Must be implemented. |
| Typed Go research API | Existing `/api/v1/experiments` uses `config map[string]any` | Missing | Must be implemented as a new typed surface, not by stretching the old free-form API. |

## Detailed Findings

### Atomic writes and stable file persistence

The repository already uses atomic temp-file replacement in:

- `go/internal/storage/jsonfile.go`
- `python/src/fl_platform/worker/task_journal.py`

This is the right baseline for immutable specification snapshots,
registry/state files, and environment/artifact manifests. The current
helper is not sufficient by itself because it:

- does not detect conflicting concurrent writers
- does not compute or validate checksums
- does not protect against silent overwrite of an existing immutable file

### Journals and append-safe storage

The strongest reusable pattern is the security journal family:

- Go security event journal
- Go security audit journal
- Python worker security event journal

These already implement:

- JSONL append-only persistence
- bounded rotation
- payload checksums
- cursor pagination
- corruption accounting

The research event and metric journals should follow the same journal
shape, but with typed schemas and SHA-256 record checksums.

### RBAC and serialization

The security API established the correct repository-wide pattern:

- broad authenticated route registration
- fine-grained permission checks inside handlers
- explicit role-aware projections

The research API should copy this model instead of the older
experiment/run handlers' inline role checks and single shared response
objects.

### Idempotency

There is already a workable request-shape precedent:

- `Idempotency-Key` header
- stable replay result when request matches
- explicit conflict when a key is reused unsafely

However, the current implementation is memory-backed and therefore not
restart-safe. The research registry requires a durable idempotency store
bound to:

- operation type
- experiment ID
- specification hash
- canonical request hash

### Storage-root conventions

The control plane already has a clear durable root convention:

- `FL_CONTROL_PLANE_DATA_DIR`
- `bootstrap.PathsForDataDir(...)`

Research registry data should be stored underneath this same root in a
dedicated subdirectory rather than inventing a second top-level storage
scheme.

## Recommendation

Build the research registry as a new, typed subsystem that:

- reuses the control-plane data root
- reuses atomic temp-file replacement patterns
- reuses security-journal-style JSONL durability
- reuses permission constants and explicit role-aware serializers
- preserves the old `/api/v1/experiments` routes for legacy callers
- introduces a separate typed research API rather than mutating the
  existing free-form contract in place
