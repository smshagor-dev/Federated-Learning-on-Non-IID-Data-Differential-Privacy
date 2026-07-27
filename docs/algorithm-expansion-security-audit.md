# Algorithm Expansion Phase Security / Privacy Boundary Audit (Work Package P)

Scope: everything added or changed for the Algorithm Expansion phase (FedSAM/Ditto/Per-FedAvg,
shared-backbone personalization, personalized model persistence, model/
dataset registries, personalized evaluation, fairness metrics, the new Go
APIs, and the new web dashboard views). Gaps carried over unchanged from
the Foundation, Aggregation Core, and Coordinator Runtime phases (coordinator TLS, secure aggregation, sample/user-level DP)
are not re-audited here — see [known-limitations.md](known-limitations.md)
for those.

Each item below states what was checked, what was found, and where the
evidence is (a file, a test, or both) — not just a checkbox.

## 1. Path traversal / filesystem boundary

| Component | Check | Result |
|---|---|---|
| `FilesystemPersonalizedModelStore` (Python) | Every path segment derived from caller input (`run_id`, `client_id`, `algorithm`) passes `_VALID_ID` (`^[A-Za-z0-9_.-]+$`) before touching the filesystem | **Pass** — `personalization/store.py`'s `_validate_id`, called from `_client_dir`/`_metadata_path`/`_artifact_path` |
| `FilesystemModelRegistry` / `FilesystemDatasetRegistry` (Python) | Same pattern for `name`/`version`/`dataset_id`/`partition_id` | **Pass** — `models/model_registry.py`'s `_path`, `datasets/dataset_registry.py`'s `_dataset_path`/`_partition_path` |
| `models.FileRepository` / `datasets.FileRepository` (Go) | Do any HTTP-supplied identifiers (`name`, `version`, `dataset_id`, `partition_id`) get used to construct a filesystem path? | **N/A — no exposure.** Go's registries persist their *entire* collection to one fixed path from `bootstrap.PathsForDataDir` per call (mirroring the pre-existing `projects`/`experiments` pattern); caller-supplied identifiers are only ever used as in-memory map keys, never path segments. Verified by reading `go/internal/models/repository.go` and `go/internal/datasets/repository.go` — neither imports `path/filepath` with a caller-controlled argument. |
| Go HTTP handlers (`model_handlers.go`, `dataset_handlers.go`) | URL path segments (`name`, `version`, `datasetID`, `partitionID`) are extracted via `strings.TrimPrefix`/`strings.Split` — do they reach any OS-level path/exec call? | **Pass — no such call exists** in either handler file. |

## 2. Unsafe deserialization

| Component | Check | Result |
|---|---|---|
| `FilesystemPersonalizedModelStore.load` | Tensor artifacts loaded via `torch.load(..., weights_only=True)` | **Pass** — restricts unpickling to tensor data only, no arbitrary object graph / code execution. See `personalization/store.py:171`. |
| `datasets/loaders.py` (`load_mnist`/`load_cifar10`) | torchvision dataset downloads — same weights_only concern? | **N/A** — these load image tensors via torchvision's own dataset classes, not raw `torch.load` on an untrusted path. |
| Go JSON decoding (`json.NewDecoder(r.Body).Decode`) | Standard library `encoding/json` — no custom `Unmarshaler` performing unsafe reflection or code execution | **Pass** — all Go request bodies decode into plain structs (`models.Model`, `datasets.Dataset`, `datasets.Partition`, request DTOs); no `interface{}`-typed fields that could smuggle executable behavior. |

## 3. Tamper / corruption detection

| Component | Check | Result |
|---|---|---|
| Personalized model artifacts | SHA-256 checksum computed at save time, re-verified at load time before `torch.load` runs | **Pass** — `personalization/store.py`'s `_sha256_hex`, checked in `load()` before deserialization, raising `PersonalizedModelCorruptionError` on mismatch (never silently loads a truncated/tampered artifact). |
| Personalized model ownership | Loaded metadata's `run_id`/`client_id` must match what the caller asked for | **Pass** — `PersonalizedModelOwnershipError` raised on mismatch; a metadata file that claims to belong to a different run/client than requested is never returned as if valid (defends against both path-traversal residue and file swaps). |
| Personalized model schema | Loaded metadata's `architecture_name`/`state_dict_schema_hash` checked against caller expectations when supplied | **Pass** — `PersonalizedModelSchemaError`; prevents loading a checkpoint produced by an incompatible model shape into the wrong architecture. |
| Model registry schema validation | `ModelRegistryEntry.validate()` only transitions DRAFT→VALIDATED if a caller-supplied *actual* schema hash (computed from a real constructed model) matches the registered hash | **Pass**, both languages — Python's `model_registry.py:104-118`, Go's `application.ModelService.Validate` (`ErrSchemaHashMismatch`). |

## 4. Authorization (RBAC)

| Route group | Required role(s) | Consistent with existing pattern? |
|---|---|---|
| `GET /api/v1/algorithms[/{name}]`, `GET /api/v1/models*`, `GET /api/v1/datasets*`, `GET .../personalization`, `.../fairness`, `.../algorithm-summary` | Viewer, Researcher, Admin, Service (read) | Yes — matches existing `GET /api/v1/coordinator/runs*` read access. |
| `POST /api/v1/models`, `POST /api/v1/models/{n}/{v}/{validate,activate,deprecate,archive}`, `POST /api/v1/datasets`, `POST /api/v1/datasets/{id}/{validate,activate,deprecate}`, `POST /api/v1/datasets/{id}/partitions` | Researcher, Admin (write) | Yes — matches existing `POST /api/v1/projects`/`POST /api/v1/experiments` write access. |
| Experiment algorithm-config validation (`validateExperimentAlgorithmConfig`) | N/A (validation, not authz) | Runs inside `ExperimentService.Create/Update`, which is already behind the Researcher/Admin write check at the HTTP layer — no new bypass introduced. |

No new role or capability was introduced this phase; all new routes reuse
the existing four roles (`RoleViewer`/`RoleResearcher`/`RoleAdmin`/`RoleService`)
and the existing `AuthService.Authorize` check. Verified by reading every
`mux.Handle` registration added to `go/internal/transport/httpapi/server.go`.

## 5. Sensitive-data exposure

* **Personalized model tensors never cross into Go or the web dashboard.**
  The Go `PersonalizationMetricRecord` (both the proto message and the Go
  struct) carries only scalars (accuracies, losses, sample counts, a
  version number, a timestamp) — never a tensor or state-dict. Verified
  against `proto/coordinator/coordinator.proto`'s message definition and
  `go/internal/coordinator/client.go`'s `PersonalizationMetricRecord`
  struct: no `bytes`/tensor-shaped field exists to leak.
  `checkpoint_reference`/`storage_reference` fields on `Model`/`Dataset`
  are opaque strings Go never dereferences into a file read.
* **The web dashboard never requests or renders a personalized model's
  actual weights** — `PersonalizationPanel` only calls
  `getFairnessWithToken`/`getPersonalizationRecordsWithToken`, both of
  which return the same scalar-only `PersonalizationMetricRecord`/
  `PersonalizationMetrics` shapes.
* **Per-client accuracy is visible to any authenticated Viewer**, not just
  the run's owner — see known-limitations.md's the Algorithm Expansion phase section for why
  this is judged consistent with (not a regression from) this project's
  existing resource-level (not field-level) RBAC granularity.

## 6. Injection

* **Go**: all new SQL is nonexistent (registries are JSON-file/in-memory
  only, no SQL). All new HTTP handlers use `encoding/json` for bodies and
  `strings.TrimPrefix`/`strings.Split` for path parsing — no
  string-concatenated queries, shell commands, or template injection
  points were introduced.
* **Python**: no new `subprocess`/`os.system`/`eval`/`exec` call sites were
  added in the Algorithm Expansion phase's algorithm/registry/personalization/evaluation
  modules (grepped for all four across
  `python/src/fl_platform/{algorithms,models,datasets,personalization,evaluation}/`
  — zero matches outside pre-existing, unrelated code).

## 7. Audit trail

Every new mutation records an audit event via the existing
`AuditService.Record` path (same as `project.create`/`run.transition`
etc.): `model.register`, `model.transition`, `dataset.register`,
`dataset.transition`, `dataset.partition.create`. No new mutation bypasses
the audit log.

## Summary

No new vulnerability class was introduced by the Algorithm Expansion phase's Go/Python
additions. The pre-existing security posture (local-development-grade
coordinator security: insecure gRPC, no TLS/mTLS, no per-worker auth
token — see [known-limitations.md](known-limitations.md)) is unchanged.
The one genuinely new risk surface — personalized models potentially
memorizing client-specific data — is called out explicitly in
known-limitations.md's the Algorithm Expansion phase section rather than left implicit, per
Work Package P's requirement.
