"""Work Packages G-M: Web Security Center browser coverage. This group
does not reimplement browser automation inside the Python harness --
that would duplicate the real Playwright suite at web/e2e/*.spec.ts.
Instead it brings up the `web` service on the same shared Docker
Compose stack, then shells out to the real `playwright test` CLI (one
subprocess per spec file, so each Work Package gets its own PASS/FAIL
rather than one opaque bundle) and asserts on the real exit code -- a
non-zero exit from Playwright means at least one real `expect(...)`
assertion failed against the live, running application.

Prerequisite this group does NOT set up itself (a one-time environment
step, not a per-run harness responsibility): `@playwright/test` and the
Chromium browser binary must already be installed --

    cd web && npm install
    cd web && node ./node_modules/playwright/cli.js install chromium --with-deps

If either is missing, the scenario below fails with Playwright's own
"executable doesn't exist" / "Cannot find module" message rather than
silently skipping, which is the correct behavior: a missing prerequisite
must surface as FAIL, not a quiet PASS.
"""

from __future__ import annotations

import os
import subprocess

from framework import REPO_ROOT, Context, Scenario, Status

WEB_DIR = REPO_ROOT / "web"
_PLAYWRIGHT_CLI = ["node", "./node_modules/playwright/cli.js", "test"]


def _run_playwright_spec(spec_file: str, work_package: str):
    def _run(ctx: Context) -> None:
        ctx.compose("up", "-d", "web", timeout=180.0)
        web_healthy = ctx.wait_for_health("http://localhost:3000/login", timeout_seconds=90.0)
        ctx.assert_true(web_healthy, "the web app becomes reachable at http://localhost:3000/login")

        env = dict(os.environ)
        env["PLAYWRIGHT_BASE_URL"] = "http://localhost:3000"
        env["PLAYWRIGHT_API_BASE_URL"] = ctx.api_base
        result = subprocess.run(
            [*_PLAYWRIGHT_CLI, spec_file],
            cwd=WEB_DIR,
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        tail = (result.stdout + result.stderr)[-1500:]
        ctx.assert_true(
            result.returncode == 0,
            f"{work_package}: `playwright test {spec_file}` exits 0 (real browser "
            f"assertions against the live stack), got exit {result.returncode}: {tail}",
        )

    return _run


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="security-ui.overview.browser-suite",
        name="Work Package I: Security Overview browser suite passes",
        category="security-ui",
        description="e2e/security-overview.spec.ts against the real, live Web Security Center.",
        required_services=("coordinator", "api", "web"),
        prerequisites="stack up; Playwright + chromium installed in web/",
        assertion="playwright test e2e/security-overview.spec.ts exits 0",
        expected_result="0",
        timeout_seconds=180.0,
        cleanup="none (read-only scenarios)",
        required=True,
        support_status=Status.SKIPPED,
        run=_run_playwright_spec("e2e/security-overview.spec.ts", "Work Package I"),
    ),
    Scenario(
        scenario_id="security-ui.worker-administration.browser-suite",
        name="Work Package J: Worker Administration browser suite passes",
        category="security-ui",
        description=(
            "e2e/worker-administration.spec.ts: real worker list, worker detail, and a "
            "real reversible suspend->activate cycle on worker-1 through the live API."
        ),
        required_services=("coordinator", "api", "web", "python-worker"),
        prerequisites="stack up; worker-1 already registered; Playwright + chromium installed",
        assertion="playwright test e2e/worker-administration.spec.ts exits 0",
        expected_result="0",
        timeout_seconds=180.0,
        cleanup="worker-1 is restored to active by the spec itself",
        required=True,
        support_status=Status.SKIPPED,
        run=_run_playwright_spec("e2e/worker-administration.spec.ts", "Work Package J"),
    ),
    Scenario(
        scenario_id="security-ui.coordinator-keys.browser-suite",
        name="Work Package K: Coordinator-Key admin browser suite passes",
        category="security-ui",
        description=(
            "e2e/coordinator-keys.spec.ts: real key table, a real additive rotation "
            "(old key moves to grace period, stays trusted), and a revoke-dialog check "
            "that opens then cancels rather than executing a destructive revoke of the "
            "active key against a shared stack."
        ),
        required_services=("coordinator", "api", "web"),
        prerequisites="stack up; Playwright + chromium installed",
        assertion="playwright test e2e/coordinator-keys.spec.ts exits 0",
        expected_result="0",
        timeout_seconds=180.0,
        cleanup="a real new coordinator signing key is left active; the previous key is left in grace period",
        required=True,
        support_status=Status.SKIPPED,
        run=_run_playwright_spec("e2e/coordinator-keys.spec.ts", "Work Package K"),
    ),
    Scenario(
        scenario_id="security-ui.event-explorer.browser-suite",
        name="Work Package L: Event Explorer browser suite passes",
        category="security-ui",
        description="e2e/event-explorer.spec.ts: real live-polled event feed and filters.",
        required_services=("coordinator", "api", "web", "python-worker"),
        prerequisites="stack up; at least one WORKER_REGISTERED event already emitted",
        assertion="playwright test e2e/event-explorer.spec.ts exits 0",
        expected_result="0",
        timeout_seconds=180.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_run_playwright_spec("e2e/event-explorer.spec.ts", "Work Package L"),
    ),
    Scenario(
        scenario_id="security-ui.audit-explorer.browser-suite",
        name="Work Package M: Audit Explorer browser suite passes",
        category="security-ui",
        description=(
            "e2e/audit-explorer.spec.ts: produces one real, reversible worker "
            "suspend->activate audit record itself, then verifies it is durably readable."
        ),
        required_services=("coordinator", "api", "web", "python-worker"),
        prerequisites="stack up; Playwright + chromium installed",
        assertion="playwright test e2e/audit-explorer.spec.ts exits 0",
        expected_result="0",
        timeout_seconds=180.0,
        cleanup="worker-1 is restored to active by the spec itself",
        required=True,
        support_status=Status.SKIPPED,
        run=_run_playwright_spec("e2e/audit-explorer.spec.ts", "Work Package M"),
    ),
    Scenario(
        scenario_id="security-ui.secure-user-level-dp-privacy.browser-suite",
        name="Secure User-Level DP Operations, Observability, and Release Evidence slice, Work Area O: browser suite passes",
        category="security-ui",
        description=(
            "e2e/secure-user-level-dp-privacy.spec.ts: capability/health sections, "
            "the 10 mandatory limitation warnings, and per-role (admin/viewer/service) "
            "access against the real running backend."
        ),
        required_services=("coordinator", "api", "web"),
        prerequisites="stack up; Playwright + chromium installed",
        assertion="playwright test e2e/secure-user-level-dp-privacy.spec.ts exits 0",
        expected_result="0",
        timeout_seconds=180.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_run_playwright_spec("e2e/secure-user-level-dp-privacy.spec.ts", "Work Area O"),
    ),
]
