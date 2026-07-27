# Security CI

**Status: Implemented, not yet observed on a real GitHub Actions run**
(added and locally validated by direct invocation of the same commands
each job runs; not yet exercised by an actual push/PR/scheduled trigger
on GitHub's infrastructure). Security Runtime Completion and Release
Evidence slice.

## Two gates, by design

- **`security-runtime-pr`** (`.github/workflows/ci.yml`): runs on every
  push/PR, alongside every other existing CI job. A required, fast
  subset of the live runtime-validation harness —
  `transport,security-api,metrics,event-journal,audit-journal,
  regression` — bringing up the real Compose mTLS stack
  (`postgres`+`redis`+`coordinator`+`api`+`python-worker`) and running
  real scenarios against it, not mocks. Deliberately excludes the
  slower worker-lifecycle/event-centralization groups (which poll for
  real registration/delivery timing) and the browser suite, so every
  PR still gets fast, real coverage of the parts most likely to break
  silently without paying for a full ~10+ minute run on every commit.
- **`security-runtime-full`** (`.github/workflows/security-runtime-
  full.yml`): scheduled daily (`cron: "0 6 * * *"`) plus
  `workflow_dispatch`. Every group in the registry, including the
  `security-ui` browser suite (installs Chromium via Playwright's own
  installer, builds the `web` image too). Named for what it runs, not
  "scheduled-validation" or similar vague/roadmap-style language —
  this repository's terminology policy
  (`scripts/check_project_terminology.py`) rejects that.

Both jobs:

1. Generate a fresh development PKI (`generate-dev-ca.sh`,
   `issue-service-cert.sh coordinator`, `issue-service-cert.sh go-api`,
   `issue-worker-cert.sh worker-1`) — CI has no persistent `certs/dev/`
   (gitignored, generated per-run, matching local development).
2. Build the required Compose images.
3. Run the harness (`scripts/security-validation/run.py`) with the
   selected `--group` list.
4. Run `scripts/security-validation/check_artifact_sanitation.py`
   against the harness's own output directory, `if: always()` (so a
   failed run's partial output is still checked before upload).
5. Upload the sanitized `summary.json`/`summary.md` (and, for the full
   workflow, Playwright's `results.json` list-reporter output only —
   never the trace/screenshot artifacts, see below) as a build
   artifact.

## What is never uploaded

Raw Playwright trace `.zip` files and failure screenshots
(`web/e2e-results/artifacts/`) are deliberately **not** uploaded by
either workflow. `check_artifact_sanitation.py` can verify text-based
output (JSON/Markdown/log files) for prohibited patterns, but cannot
inspect the *content* of a screenshot or a trace archive the same way —
see that script's own disclosed scope. A failure screenshot of an
unexpected app state is not guaranteed secret-free the way this
harness's own redacted summaries are (verified via `_redact()` in
`scripts/security-validation/framework.py`, plus the independent
`check_artifact_sanitation.py` re-check), so this repository's
artifact-sanitation policy excludes them rather than trusting an
unverified binary blob. The list-reporter console output remains
visible in the job log for debugging a real browser-suite failure.

## Secure User-Level DP artifact allow/deny list (Work Area W)

Extending the same policy above for the new `secure-aggregation-user-
level-dp` scenario group and `scripts/validate_secure_user_level_dp.py`:

**Permitted** (same shape as every other group's artifacts): sanitized
`summary.json`/`summary.md`, the coverage table in
`docs/secure-user-level-operations-audit.md`, the benchmark/statistical-
noise-smoke report text `secure_random_test.cpp` prints to stdout (draw
count/mean/variance/tolerance/provider/build-type only — no raw draws),
redacted Playwright screenshots/traces under the same "never uploaded"
rule above, `GET /metrics`'s `fl_secure_user_dp_*` snapshot, environment
reports.

**Forbidden, checked by `check_artifact_sanitation.py`'s extended
pattern list**: a clear (unclipped or clipped) update, an individual
norm or clipping factor, an individual client's weight, a noise tensor
or noise-generator state, a masked tensor byte, a pairwise shared
secret, a worker's `own_private_key_raw`/derived mask-stream key, a
signing key, a nonce, or a dataset sample. The private-key/shared-
secret/mask-key pattern is new this slice (`own_private_key_raw`,
`private_key_raw`, `shared_secret`, `mask_key`, `mask_stream_key` as a
hex-valued JSON field) — every other category was already covered
generically by the existing signature/payload_hash/PEM/Bearer-token
patterns, since this slice introduces no new sensitive-data *shape*
beyond what those already catch.

## Relationship to the existing `secret-scan` job

`secret-scan` (added in an earlier slice) scans every **git-tracked**
file in the repository for private-key/credential markers on every
push/PR — a static, whole-repository check. `check_artifact_sanitation.py`
is a different, narrower check: it scans the harness's own **generated
output** (never committed) before that output is uploaded as a CI
artifact or assembled into a release-evidence bundle. The two do not
overlap in scope and both remain necessary.

## Local equivalents

```bash
# PR-subset equivalent
python scripts/security-validation/run.py \
  --group transport,security-api,metrics,event-journal,audit-journal,regression

# Full-matrix equivalent (includes the browser suite; requires
# `web/node_modules` and a Chromium install -- see docs/security-ui-report.md)
python scripts/security-validation/run.py

# Artifact sanitation
python scripts/security-validation/check_artifact_sanitation.py artifacts/security-runtime-validation

# Release evidence
python scripts/generate_release_evidence.py
```

## Validation

Live-checked this slice by running the exact commands each job runs,
directly (not via `act` or another local-GitHub-Actions runner — none
is available in this environment): PKI generation, image builds, the
full 94-scenario harness (37 PASS / 0 FAIL / 0 BLOCKED / 57 DEFERRED),
artifact sanitation (`OK: 4 file(s) scanned, no prohibited material
found.`), and the release-evidence generator (`OK: 9 file(s) scanned,
no prohibited material found.`) all passed. See
[security-runtime-completion-report.md](security-runtime-completion-report.md)
for the full fresh-evidence report.
