# Risk Register

| ID | Risk | Impact | Likelihood | Current Mitigation | Future Mitigation |
|---|---|---|---|---|---|
| R1 | Sequential client execution does not scale | High | High | Documented baseline limitation | Move coordination and aggregation into C++ with worker pool |
| R2 | Legacy privacy model may be misinterpreted as sample-level DP | High | Medium | Explicit privacy audit | Separate privacy modes and ledgers |
| R3 | Root-level prototype and new monorepo can drift | Medium | Medium | Legacy copy created and documented | Introduce compatibility gates and deprecation plan |
| R4 | Artifact filenames can be overwritten between runs | Medium | Medium | Manual results folder management | Add run IDs and artifact manifests |
| R5 | No checkpoint recovery | High | Medium | None | Add coordinator checkpointing in later milestones |
| R6 | No auth or multi-user isolation | High | High | Legacy is treated as local-only | Add Go control plane auth/RBAC |
| R7 | Tensor payloads are unvalidated | High | Medium | Single-process trust boundary | Add manifests, checksums, validators in C++ core |
| R8 | Large infrastructure scope can cause milestone sprawl | High | High | Milestone 1 constrained to scaffolding | Release gates per milestone |
| R9 | Toolchain availability may block full validation | Medium | Medium | Record exact unavailable commands | Provision CI containers with full toolchain |
| R10 | Legacy GUI and new web dashboard expectations may diverge | Medium | Medium | Keep Tkinter only as legacy | Shift primary interface after web parity |
