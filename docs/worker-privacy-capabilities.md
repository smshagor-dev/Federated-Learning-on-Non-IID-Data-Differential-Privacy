# Worker Privacy Capability Advertisement

**Status: implemented & tested across all three languages.** Source:
`WorkerPrivacyCapabilities` (proto: `proto/privacy/privacy.proto`),
`fl::coordinator::WorkerCapability::privacy`
(`cpp/coordinator/include/fl_coordinator/worker_registry.hpp`),
`RunInstance::acquire_task`'s compatibility gate
(`cpp/coordinator/src/run_manager.cpp`),
`fl_platform.privacy.accounting.opacus_capabilities`
(`python/src/fl_platform/privacy/accounting.py`),
`GrpcCoordinatorClient.register_worker`
(`python/src/fl_platform/worker/coordinator_client.py`). Tests:
`cpp/coordinator/tests/worker_privacy_capability_test.cpp`,
`cpp/coordinator/tests/hybrid_dp_test.cpp`,
`python/tests/test_grpc_coordinator_client.py`,
`python/tests/test_privacy_accounting.py`.

## The problem this solves

Sample-level DP (see [privacy-mathematics.md](privacy-mathematics.md))
is implemented by the Python worker via Opacus — but Opacus is an
optional heavy dependency, not guaranteed to be installed in every
worker's environment. Without a capability check, the coordinator could
dispatch a sample-level-DP task to a worker that lacks Opacus, and that
worker would face a choice between crashing or (worse) silently training
without privacy protection while reporting success. The Critical Privacy
Rule's spirit — never let a system silently fail to protect what it
claimed to protect — extends to this: **a privacy-requiring task must
never be assigned to a worker that cannot actually honor it**, and the
system must never fall back to non-private execution as a silent rescue.

## The capabilities message

```protobuf
message WorkerPrivacyCapabilities {
  bool supports_sample_level_dp = 1;
  string opacus_version = 2;
  repeated AccountantType supported_accountants = 3;
  bool supports_secure_random = 4;
}
```

Sent once, as part of `RegisterWorkerRequest.privacy`, when a worker
registers with the coordinator (capabilities are worker-process-level,
not per-run).

## Truthful, not optimistic

`opacus_capabilities()` probes `importlib.metadata.version("opacus")` —
a real installed-package check, not a hardcoded `True` or a check of
what the worker *should* have. It deliberately uses `importlib.metadata`
rather than `import opacus` directly, so calling it doesn't pay Opacus's
import cost for a worker that will never train privately. `supported_accountants`
is populated only when `opacus_capabilities()` reports installed — an
empty list otherwise, never a hopeful guess. `supports_secure_random` is
hardcoded `False` on both the Python and C++ sides: neither uses a
CSPRNG for privacy noise (see
[privacy-engineering-security-audit.md](privacy-engineering-security-audit.md)),
and the field exists specifically so that claim is never made by
accident.

A worker that never sent a `privacy` field at all (an older or
non-privacy-aware worker) gets a default-constructed
`WorkerPrivacyCapabilities` — `supports_sample_level_dp = false` — which
`acquire_task`'s gate treats identically to an explicit `false`, not as
"unknown, assume compatible."

## Compatible-worker-only task assignment

`RunInstance::acquire_task` (`run_manager.cpp`) checks, before handing a
task to a requesting worker:

```cpp
const bool sample_level_dp_required =
    config_.privacy_mode == fl::core::PrivacyMode::kSampleLevelDp ||
    config_.privacy_mode == fl::core::PrivacyMode::kHybridDp;
if (sample_level_dp_required) {
    const auto worker_info = worker_registry_->get(worker_id);
    if (!worker_info.has_value() || !worker_info->capability.privacy.supports_sample_level_dp) {
        return std::nullopt;
    }
}
```

Returning `std::nullopt` here means "no task for you right now" — the
same response an incompatible-model-format or busy worker gets, not an
error. This is deliberate: an incompatible worker simply never receives
tasks from a sample-level/hybrid-DP run and the run waits for a
compatible one, rather than the run failing outright the moment an
incompatible worker happens to poll first. User-level-only DP runs
impose no such gate — user-level DP is computed entirely by the
coordinator, so any worker can participate.

## Discoverability

Go's `ListWorkers` passes each worker's advertised
`WorkerPrivacyCapabilities` through to the API/web layer unmodified, so
an operator can see *why* a sample-level-DP run isn't progressing (no
registered worker advertises support) rather than only observing that it
isn't. This is a separate concern from
[privacy-compatibility-matrix.md](privacy-compatibility-matrix.md)'s
static algorithm-compatibility table — that table answers "does this
algorithm support this mechanism at all," while worker capabilities
answer "is there a worker present right now that can execute it."
