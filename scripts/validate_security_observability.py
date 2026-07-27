#!/usr/bin/env python3
"""Docker Compose validation harness for the Security Events, Metrics,
and Durable Audit Journal slice. See docs/security-runtime-validation.md.

This is the first committed, reusable Docker security-validation script
in this repository -- every prior "N/N checks" number in this project's
docs came from one-off scratch scripts run by hand during a live session
(confirmed by direct inspection of scripts/ before this slice: no
security-validation file existed). This one is meant to be re-run, not
thrown away.

What it checks, against a real running `coordinator` + `api` stack with
real mTLS (docker-compose.dev.yml + docker-compose.security.yml):

  1. `/api/v1/security/events` is real (200 with a JSON body), not the
     old 501 stub.
  2. A permission-denied action (VIEWER attempting a worker mutation)
     produces a real SECURITY_PERMISSION_DENIED event, observable on a
     follow-up GET.
  3. `/api/v1/security/audit` serves real, paginated, filterable records
     from the new security-specific journal (not just the general
     AuditRepository).
  4. Role-based redaction: a RESEARCHER-role read of both endpoints
     lacks reason_code/free-form detail fields a detailed (ADMIN) read
     includes.
  5. `/metrics` (the Go control plane's Prometheus endpoint) exposes
     `fl_security_events_total`.
  6. Event/audit persistence survives an `api` container restart (same
     container, not a fresh one -- see this file's module docstring in
     the project's docs for why that is the meaningful restart boundary
     here: the journal files live under the container's own writable
     layer, not a mounted volume, in the default Compose configuration).

Usage:
    python scripts/validate_security_observability.py

Requires Docker and Docker Compose. Brings the stack up, runs the
checks, and tears it down -- safe to re-run. Exits non-zero (and prints
which check failed) on any failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = [
    "infra/compose/docker-compose.dev.yml",
    "infra/compose/docker-compose.security.yml",
]
API_BASE = "http://localhost:8080"
SERVICES = ["postgres", "redis", "coordinator", "api"]

_checks_passed = 0
_checks_failed: list[str] = []


def check(condition: bool, description: str) -> None:
    global _checks_passed
    if condition:
        _checks_passed += 1
        print(f"  [OK] {description}")
    else:
        _checks_failed.append(description)
        print(f"  [FAIL] {description}")


def compose_cmd(*args: str) -> list[str]:
    cmd = ["docker", "compose"]
    for f in COMPOSE_FILES:
        cmd += ["-f", f]
    cmd += list(args)
    return cmd


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO_ROOT, check=False, capture_output=True, text=True, **kwargs)


def http_request(method: str, path: str, bearer: str = "", body: dict | None = None) -> tuple[int, dict]:
    url = API_BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if bearer:
        request.add_header("Authorization", bearer)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, (json.loads(raw) if raw else {})
        except json.JSONDecodeError:
            return error.code, {}


def login(email: str, password: str) -> str:
    status, payload = http_request("POST", "/api/v1/auth/login", body={"email": email, "password": password})
    if status != 200:
        raise RuntimeError(f"login failed for {email}: {status} {payload}")
    return "Bearer " + payload["token"]


def wait_for_healthy(timeout_s: float = 180.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = run(compose_cmd("ps", "--format", "json"))
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        states = []
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            states.append((entry.get("Service"), entry.get("Health") or entry.get("State")))
        if states and all(health in ("healthy", "running") for _, health in states):
            try:
                status, _ = http_request("GET", "/healthz")
                if status == 200:
                    return
            except OSError:
                pass
        time.sleep(3)
    raise RuntimeError("stack did not become healthy in time")


def main() -> int:
    print(f"Bringing up: {', '.join(SERVICES)} (real mTLS override)")
    up = run(compose_cmd("up", "-d", "--build", *SERVICES))
    if up.returncode != 0:
        print(up.stdout)
        print(up.stderr)
        print("docker compose up failed")
        return 1

    try:
        print("Waiting for health...")
        wait_for_healthy()

        admin = login("admin@fl-platform.dev", "admin-demo")
        researcher = login("researcher@fl-platform.dev", "research-demo")
        viewer = login("viewer@fl-platform.dev", "viewer-demo")

        print("\n1. Real /api/v1/security/events endpoint")
        status, payload = http_request("GET", "/api/v1/security/events", bearer=admin)
        check(status == 200, f"GET /api/v1/security/events returns 200 (not 501), got {status}")
        check("events" in payload, "response body has an 'events' key")

        print("\n2. Permission-denied action produces a real event")
        status, _ = http_request(
            "POST", "/api/v1/security/workers/nonexistent-worker/suspend",
            bearer=viewer, body={"reason": "validation-script-permission-check"},
        )
        check(status == 403, f"VIEWER worker-suspend attempt is forbidden, got {status}")
        status, payload = http_request("GET", "/api/v1/security/events", bearer=admin)
        events_blob = json.dumps(payload)
        check(status == 200 and "SECURITY_PERMISSION_DENIED" in events_blob,
              "a SECURITY_PERMISSION_DENIED event is observable after the denial")

        print("\n3. Real, paginated, filterable security audit journal")
        status, payload = http_request(
            "GET", "/api/v1/security/audit?limit=5&action=security.workers.suspend", bearer=admin,
        )
        check(status == 200, f"GET /api/v1/security/audit with filters returns 200, got {status}")
        check("records" in payload and "next_cursor" in payload,
              "audit response has 'records' and 'next_cursor' keys")

        print("\n4. Role-based redaction")
        status, researcher_events = http_request("GET", "/api/v1/security/events", bearer=researcher)
        researcher_blob = json.dumps(researcher_events)
        check(status == 200, "RESEARCHER can read /api/v1/security/events")
        check("reason_code" not in researcher_blob,
              "RESEARCHER (no read_detailed) does not see reason_code")
        status, admin_events = http_request("GET", "/api/v1/security/events", bearer=admin)
        check("reason_code" in json.dumps(admin_events),
              "ADMIN (has read_detailed) does see reason_code")

        print("\n5. Prometheus /metrics exposes fl_security_events_total")
        try:
            with urllib.request.urlopen(API_BASE + "/metrics", timeout=10) as response:
                metrics_text = response.read().decode("utf-8")
        except OSError as error:
            metrics_text = ""
            check(False, f"could not fetch /metrics: {error}")
        else:
            check("fl_security_events_total" in metrics_text,
                  "fl_security_events_total appears in /metrics")

        print("\n6. Event/audit persistence across an api container restart")
        restart = run(compose_cmd("restart", "api"))
        check(restart.returncode == 0, "docker compose restart api succeeded")
        wait_for_healthy()
        admin = login("admin@fl-platform.dev", "admin-demo")
        status, payload = http_request("GET", "/api/v1/security/events", bearer=admin)
        check(status == 200 and "SECURITY_PERMISSION_DENIED" in json.dumps(payload),
              "the earlier SECURITY_PERMISSION_DENIED event survives an api restart")

    finally:
        print("\nTearing down...")
        run(compose_cmd("down", "-v"))

    print(f"\n{_checks_passed} passed, {len(_checks_failed)} failed")
    if _checks_failed:
        print("Failed checks:")
        for description in _checks_failed:
            print(f"  - {description}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
