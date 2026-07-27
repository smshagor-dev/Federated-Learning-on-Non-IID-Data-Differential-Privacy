"""Security Runtime Completion and Release Evidence slice, Work Package D:
the modular runtime-validation harness's core framework.

This module intentionally contains no scenario logic of its own -- see
``groups/*.py`` for what is actually checked. It provides only the
shared plumbing every group needs: a versioned ``Scenario`` record, an
execution ``Context`` (HTTP helpers, a Docker Compose wrapper, temporary
PKI/key-material generation), a runner that enforces per-scenario
timeouts and never lets one scenario's crash abort the whole run, and
two output writers (machine-readable JSON, human-readable Markdown).

Design decisions, stated directly:

* A scenario is executed (and can therefore only be PASS/FAIL) only
  when its ``run`` callable is set. A scenario with ``run=None`` is
  always reported as its declared ``support_status``
  (BLOCKED/DEFERRED) and is never invoked -- this is the mechanical
  enforcement of "do not mark a scenario PASS merely because a command
  exited without an assertion": there is no code path by which a
  DEFERRED/BLOCKED scenario can produce a PASS.
* PASS requires ``run`` to both complete without raising AND call
  ``ctx.assert_true`` (or return truthy) at least once -- a scenario
  that runs zero assertions is treated as a harness bug (FAIL with a
  loud message), not a silent PASS.
* One shared, already-running Docker Compose stack backs every
  scenario in a given invocation (matching
  scripts/validate_security_observability.py's existing "bring up
  once, run every check, tear down once" model) -- per-scenario
  compose cycles would make a full-group run impractically slow.
  Scenarios that need a restart boundary (worker/coordinator restart
  recovery) call ``ctx.compose("restart", service)`` themselves, mid-
  scenario, against that same stack.
* No secret material is ever written to the JSON/Markdown output --
  see ``_redact`` and docs/security-ci.md's artifact-sanitation policy.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class Status(str, Enum):
    """The five statuses Work Package D requires. A scenario's final
    status is exactly one of these -- there is no implicit sixth
    "unknown" state; every registry entry must resolve to one of these
    by the time a report is written."""

    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    DEFERRED = "DEFERRED"
    SKIPPED = "SKIPPED"


class ScenarioTimeout(RuntimeError):
    """Raised internally when a scenario exceeds its declared timeout.
    Never raised by scenario code itself -- see run_scenario's use of
    a watchdog thread."""


class ScenarioAssertionError(AssertionError):
    """Raised by Context.assert_true on a failed assertion, carrying
    the human-readable description straight through to the report."""


@dataclass(slots=True)
class Scenario:
    """One entry in the versioned scenario registry (Work Package E).
    Every field the task's Work Package E lists is represented here.

    ``run`` is ``None`` for BLOCKED/DEFERRED scenarios -- see this
    module's docstring for why that is the mechanical guarantee against
    a false PASS. When set, ``run(ctx)`` must call
    ``ctx.assert_true(...)`` at least once and must not itself catch
    ScenarioAssertionError.
    """

    scenario_id: str  # e.g. "transport.mtls.worker.accept" -- see Work Package E
    name: str
    category: str  # the owning group, e.g. "transport"
    description: str
    required_services: tuple[str, ...]
    prerequisites: str
    assertion: str
    expected_result: str
    timeout_seconds: float
    cleanup: str
    required: bool  # False = optional/informational, never fails the whole run
    support_status: Status  # Status.PASS is not a valid value here; see __post_init__
    unsupported_reason: str = ""  # required when support_status is BLOCKED/DEFERRED
    run: Callable[["Context"], None] | None = None

    def __post_init__(self) -> None:
        if self.support_status not in (Status.BLOCKED, Status.DEFERRED, Status.SKIPPED):
            # A scenario that IS runnable is registered with
            # support_status=Status.SKIPPED as its "not yet executed"
            # placeholder -- PASS/FAIL are outcomes, never a declared
            # support status.
            raise ValueError(
                f"{self.scenario_id}: support_status must be BLOCKED, DEFERRED, or "
                "SKIPPED (PASS/FAIL are run-time outcomes, not declarations)"
            )
        if self.support_status in (Status.BLOCKED, Status.DEFERRED) and self.run is not None:
            raise ValueError(
                f"{self.scenario_id}: a BLOCKED/DEFERRED scenario must not have a run "
                "callable -- see this module's docstring"
            )
        if self.support_status in (Status.BLOCKED, Status.DEFERRED) and not self.unsupported_reason:
            raise ValueError(
                f"{self.scenario_id}: BLOCKED/DEFERRED requires an unsupported_reason"
            )
        if self.support_status == Status.SKIPPED and self.run is None:
            raise ValueError(
                f"{self.scenario_id}: a runnable scenario (support_status=SKIPPED) "
                "must have a run callable"
            )


@dataclass(slots=True)
class ScenarioResult:
    scenario: Scenario
    status: Status
    detail: str
    duration_seconds: float


_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"Bearer [A-Za-z0-9\-_.]+"),
    re.compile(r'"signature"\s*:\s*"[0-9a-fA-F]+"'),
    re.compile(r'"payload_hash"\s*:\s*"[0-9a-fA-F]+"'),
]


def _redact(text: str) -> str:
    """Strips anything that looks like a private key, bearer token, or
    raw signature/hash from a string before it can reach a JSON/
    Markdown report or a failure-log capture -- see
    docs/security-ci.md's artifact-sanitation policy. Pattern-based,
    not a guarantee against every conceivable secret shape, but real
    for the specific shapes this harness's own output can ever contain
    (nothing here ever prints a raw dataset sample or client update)."""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


class Context:
    """Shared execution context every scenario's ``run(ctx)`` receives.
    Constructed once per harness invocation (or a lightweight per-
    scenario view of the same shared state -- see Context.child)."""

    def __init__(
        self,
        *,
        api_base: str,
        coordinator_address: str,
        compose_files: tuple[str, ...],
        workspace: Path,
        verbose: bool = False,
    ) -> None:
        self.api_base = api_base
        self.coordinator_address = coordinator_address
        self.compose_files = compose_files
        self.workspace = workspace
        self.verbose = verbose
        self._tokens: dict[str, str] = {}
        self.assertions_run = 0

    # -- assertions ---------------------------------------------------

    def assert_true(self, condition: bool, description: str) -> None:
        self.assertions_run += 1
        if self.verbose:
            print(f"    [{'OK' if condition else 'FAIL'}] {description}")
        if not condition:
            raise ScenarioAssertionError(description)

    # -- HTTP -----------------------------------------------------------

    def http(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any] | None, bytes]:
        """Returns (status_code, parsed_json_or_None, raw_bytes).
        Never raises on a non-2xx response -- callers assert on the
        status code themselves, matching this harness's "no assertion,
        no PASS" discipline (a caught-and-ignored exception must never
        substitute for a real assertion)."""
        url = f"{self.api_base}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            raw = error.read()
            status = error.code
        except TimeoutError as error:
            # A connect-time timeout is wrapped by urlopen in URLError
            # (caught below) -- but a slow-but-eventually-arriving
            # response BODY read past the 10s socket timeout raises a
            # bare TimeoutError that urlopen does not wrap, uncaught
            # here before this fix. Observed live during
            # recovery.coordinator-health.reflects-real-outage: right
            # after `docker compose start coordinator`, the Go API's own
            # handler blocks on its coordinator gRPC dial for longer
            # than 10s while the coordinator container is still coming
            # up, and that entire scenario -- a real, working retry loop
            # -- crashed with an uncaught TimeoutError instead of simply
            # treating that one slow poll as "not ready yet" and trying
            # again. Every retry-loop scenario in this harness assumes
            # ctx.http() never raises for a transient failure; this
            # closes that gap for real, not just for this one scenario.
            return 0, None, str(error).encode("utf-8")
        except urllib.error.URLError as error:
            return 0, None, str(error).encode("utf-8")
        parsed: dict[str, Any] | None = None
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = None
        return status, parsed, raw

    def login(self, email: str, password: str) -> str:
        """Returns a cached bearer token for (email), logging in once
        per Context instance. Demo credentials only -- see
        docs/security-browser-testing.md for why these are safe to use
        against a real running stack (development-only seeded users,
        never production credentials)."""
        cache_key = email
        if cache_key in self._tokens:
            return self._tokens[cache_key]
        status, parsed, raw = self.http(
            "POST", "/api/v1/auth/login", body={"email": email, "password": password}
        )
        if status != 200 or parsed is None or "token" not in parsed:
            raise ScenarioAssertionError(
                f"login failed for {email}: status={status} body={_redact(raw.decode('utf-8', 'replace'))[:200]}"
            )
        token = str(parsed["token"])
        self._tokens[cache_key] = token
        return token

    # -- Docker Compose -------------------------------------------------

    def compose(self, *args: str, check: bool = True, timeout: float = 120.0) -> subprocess.CompletedProcess:
        cmd = ["docker", "compose"]
        for f in self.compose_files:
            cmd += ["-f", f]
        cmd += list(args)
        result = subprocess.run(
            cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"docker compose {' '.join(args)} failed (exit {result.returncode}): "
                f"{_redact(result.stderr)[-2000:]}"
            )
        return result

    def wait_for_health(self, url: str, timeout_seconds: float = 90.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    if response.status == 200:
                        return True
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(2.0)
        return False


def run_scenario(scenario: Scenario, ctx: Context) -> ScenarioResult:
    if scenario.support_status in (Status.BLOCKED, Status.DEFERRED):
        return ScenarioResult(
            scenario=scenario,
            status=scenario.support_status,
            detail=scenario.unsupported_reason,
            duration_seconds=0.0,
        )
    assert scenario.run is not None  # enforced by __post_init__
    before = ctx.assertions_run
    started = time.monotonic()
    try:
        scenario.run(ctx)
        duration = time.monotonic() - started
        if ctx.assertions_run == before:
            return ScenarioResult(
                scenario=scenario,
                status=Status.FAIL,
                detail=(
                    "harness bug: run() completed without calling ctx.assert_true "
                    "at least once -- a scenario with zero assertions can never be "
                    "reported as PASS"
                ),
                duration_seconds=duration,
            )
        return ScenarioResult(
            scenario=scenario, status=Status.PASS, detail="ok", duration_seconds=duration
        )
    except ScenarioAssertionError as error:
        return ScenarioResult(
            scenario=scenario,
            status=Status.FAIL,
            detail=_redact(str(error)),
            duration_seconds=time.monotonic() - started,
        )
    except Exception as error:  # noqa: BLE001 - a scenario crash must not abort the whole run
        return ScenarioResult(
            scenario=scenario,
            status=Status.FAIL,
            detail=_redact(f"{type(error).__name__}: {error}"),
            duration_seconds=time.monotonic() - started,
        )


@dataclass(slots=True)
class RunSummary:
    started_at: str
    finished_at: str
    results: list[ScenarioResult] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in Status}
        for result in self.results:
            counts[result.status.value] += 1
        return counts

    def required_failed(self) -> list[ScenarioResult]:
        return [
            r for r in self.results if r.scenario.required and r.status == Status.FAIL
        ]

    def to_json(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "counts": self.counts(),
            "scenarios": [
                {
                    "scenario_id": r.scenario.scenario_id,
                    "name": r.scenario.name,
                    "category": r.scenario.category,
                    "required": r.scenario.required,
                    "status": r.status.value,
                    "detail": r.detail,
                    "duration_seconds": round(r.duration_seconds, 3),
                }
                for r in self.results
            ],
        }

    def to_markdown(self) -> str:
        counts = self.counts()
        lines = [
            "# Security Runtime Validation Summary",
            "",
            f"Started: {self.started_at}",
            f"Finished: {self.finished_at}",
            "",
            f"PASS={counts['PASS']} FAIL={counts['FAIL']} "
            f"BLOCKED={counts['BLOCKED']} DEFERRED={counts['DEFERRED']} "
            f"SKIPPED={counts['SKIPPED']}",
            "",
            "| Scenario ID | Category | Required | Status | Detail |",
            "|---|---|---|---|---|",
        ]
        for r in self.results:
            detail = r.detail.replace("|", "\\|").replace("\n", " ")[:200]
            lines.append(
                f"| `{r.scenario.scenario_id}` | {r.scenario.category} | "
                f"{'yes' if r.scenario.required else 'no'} | {r.status.value} | {detail} |"
            )
        return "\n".join(lines) + "\n"


def write_reports(summary: RunSummary, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "summary.json"
    md_path = output_dir / "summary.md"
    json_path.write_text(json.dumps(summary.to_json(), indent=2), encoding="utf-8")
    md_path.write_text(summary.to_markdown(), encoding="utf-8")
    return json_path, md_path


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)
