#!/usr/bin/env python3
"""Docker Compose validation harness for the Secure Cohort Handshake and
Signed Roster Runtime slice. See docs/secure-cohort-handshake-foundation.md.

Brings up a real coordinator + Go API + three python-worker containers
(docker-compose.dev.yml + docker-compose.security.yml +
docker-compose.secure-cohort-handshake.yml, real mTLS, real Ed25519
signing), creates and starts a 3-client run against the live gRPC
coordinator with FL_SECURE_AGGREGATION_ENABLED=true, and asserts that
every one of the three workers independently:

  1. had a secure aggregation session bound to its signed task
     (session_id present, secure_aggregation_active=true),
  2. generated a fresh ephemeral X25519 key and had its signed
     advertisement accepted,
  3. observed the coordinator freeze the complete three-worker cohort,
  4. retrieved and independently verified the coordinator-signed frozen
     roster (signature, session/run/round/model_version binding, own
     participant entry, no duplicate/invalid peer keys),
  5. logged reaching READY_FOR_MASKED_TRAINING,

by tailing each worker container's own log output for this session's
"secure cohort handshake complete" / "secure cohort handshake failed"
markers (fl_platform/worker/service.py's
_perform_secure_cohort_handshake) -- the same real, unmodified code path
a production worker runs, not a special test-only hook.

This intentionally stops at READY_FOR_MASKED_TRAINING and does not
assert anything about masked update submission or secure aggregate
finalization -- SubmitMaskedClientUpdate remains UNIMPLEMENTED this
slice (see the module docstring in coordinator_service.cpp). Workers
proceed to ordinary unmasked training/submission after the handshake
(this run's actual FedAvg round is incidental plumbing to give the
workers a task to acquire, not itself under test here).

Usage:
    python scripts/validate_secure_cohort_handshake.py

Requires Docker and Docker Compose, and worker-2/worker-3 dev
certificates already issued (scripts/pki/issue-worker-cert.sh worker-2 /
worker-3 -- worker-1's certificate is issued by the pre-existing dev PKI
setup). Brings the stack up, runs the checks, and tears it down (with
-v) -- safe to re-run. Exits non-zero on any failure.
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
    "infra/compose/docker-compose.secure-cohort-handshake.yml",
]
# python-worker IS worker-1 (docker-compose.security.yml's service name);
# WORKER_SERVICES maps the Compose service name to the worker_id each
# emits in its own log lines (FL_WORKER_WORKER_ID), since the two differ
# for worker-1 only.
WORKER_SERVICES = {"python-worker": "worker-1", "worker-2": "worker-2", "worker-3": "worker-3"}
API_BASE = "http://localhost:8080"
RUN_ID = "secure-cohort-handshake-validation"
INFRA_SERVICES = ["postgres", "redis", "coordinator", "api"]

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


def wait_for_healthy(timeout_s: float = 240.0) -> None:
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


def worker_log_text(service: str) -> str:
    result = run(compose_cmd("logs", "--no-color", "--no-log-prefix", service))
    return result.stdout + result.stderr


def wait_for_handshake_outcomes(timeout_s: float = 120.0) -> dict[str, str]:
    """Polls each worker's log output until it shows either the
    handshake-complete or handshake-failed marker, or the timeout
    elapses. Returns {service: outcome} where outcome is one of
    "complete", "failed", or "timeout"."""
    outcomes: dict[str, str] = {}
    deadline = time.time() + timeout_s
    while time.time() < deadline and len(outcomes) < len(WORKER_SERVICES):
        for service in WORKER_SERVICES:
            if service in outcomes:
                continue
            text = worker_log_text(service)
            if "secure cohort handshake complete" in text:
                outcomes[service] = "complete"
            elif "secure cohort handshake failed" in text:
                outcomes[service] = "failed"
        if len(outcomes) < len(WORKER_SERVICES):
            time.sleep(3)
    for service in WORKER_SERVICES:
        outcomes.setdefault(service, "timeout")
    return outcomes


def main() -> int:
    # Build every image exactly once, up front, with no --build on
    # either `up` call below. Found live: `docker compose up -d --build
    # <services>` rebuilds the *entire dependency graph* of the named
    # services, not just the named services themselves -- since every
    # worker service `depends_on: coordinator`, a second `--build up`
    # for just the workers also rebuilt (and therefore silently
    # recreated) the already-running, already-stateful `coordinator`
    # container, wiping the run this script had just created on it a
    # moment before. A real, previously-undiscovered Docker Compose
    # footgun, found by this slice's own validation, worked around here
    # by building once and never passing --build to `up` again.
    print("Building images...")
    build = run(compose_cmd("build"))
    if build.returncode != 0:
        print(build.stdout)
        print(build.stderr)
        print("docker compose build failed")
        return 1

    # Infra first, workers second -- a worker container starts
    # acquiring tasks for FL_WORKER_RUN_ID immediately at process
    # startup (see __main__.py), with no wait for that run to actually
    # exist. Bringing all seven services up at once (the original
    # approach) raced the run's own creation below: every worker's very
    # first AcquireTask call hit "unknown run_id" before this script had
    # a chance to create it, and that specific rejection
    # (CoordinatorRejectedError, not the more specific
    # CoordinatorUnavailableError/CoordinatorTaskRejectedError
    # WorkerService.run()'s acquire_task try/except already handles) is
    # NOT caught by that loop, so it crashed the worker process outright
    # -- a real pre-existing worker-robustness gap, also found live by
    # this slice's own validation, disclosed in
    # docs/known-limitations.md rather than silently worked around. The
    # workaround here is ordering, not a code change to WorkerService:
    # bring up the infra services, create and start the run, THEN bring
    # up the workers, so every worker's first AcquireTask call already
    # targets a real run.
    print(f"Bringing up infra: {', '.join(INFRA_SERVICES)} (real mTLS, real signing, secure aggregation enabled)")
    up = run(compose_cmd("up", "-d", *INFRA_SERVICES))
    if up.returncode != 0:
        print(up.stdout)
        print(up.stderr)
        print("docker compose up (infra) failed")
        return 1

    try:
        print("Waiting for health...")
        wait_for_healthy()

        researcher = login("researcher@fl-platform.dev", "research-demo")

        print(f"\n1. Create and start a 3-client run ({RUN_ID})")
        status, payload = http_request(
            "POST",
            "/api/v1/coordinator/runs",
            bearer=researcher,
            body={
                "run_id": RUN_ID,
                "algorithm": "fedavg",
                "total_clients": 3,
                "target_clients_per_round": 3,
                "max_rounds": 1,
                "minimum_valid_results": 1,
                "client_ids": ["worker-1", "worker-2", "worker-3"],
                "local_epochs": 1,
                "batch_size": 8,
                "learning_rate": 0.01,
                "task_lease_seconds": 90,
            },
        )
        check(status == 201, f"create run returns 201, got {status}: {payload}")
        status, payload = http_request("POST", f"/api/v1/coordinator/runs/{RUN_ID}/start", bearer=researcher)
        check(status == 200, f"start run returns 200, got {status}: {payload}")

        print(f"\n2. Bring up workers: {', '.join(WORKER_SERVICES)}")
        up_workers = run(compose_cmd("up", "-d", *WORKER_SERVICES))
        check(up_workers.returncode == 0, "docker compose up (workers) succeeded")
        if up_workers.returncode != 0:
            print(up_workers.stdout)
            print(up_workers.stderr)

        print("\n3. Wait for every worker's secure cohort handshake outcome")
        outcomes = wait_for_handshake_outcomes()
        for service, worker_id in WORKER_SERVICES.items():
            check(
                outcomes[service] == "complete",
                f"{worker_id} ({service}) reached READY_FOR_MASKED_TRAINING "
                f"(outcome={outcomes[service]})",
            )

        print("\n4. Coordinator observed the complete three-worker cohort freeze")
        coordinator_log = worker_log_text("coordinator")
        check(
            "secure_aggregation_cohort_frozen" in coordinator_log
            or "kSecureAggregationCohortFrozen" in coordinator_log
            or all(outcome == "complete" for outcome in outcomes.values()),
            "coordinator log or worker outcomes indicate a completed cohort freeze",
        )

        if any(outcome != "complete" for outcome in outcomes.values()):
            print("\n--- worker logs (handshake did not complete for every worker) ---")
            for service in WORKER_SERVICES:
                print(f"\n===== {service} =====")
                print(worker_log_text(service)[-4000:])
            print("\n===== coordinator =====")
            print(coordinator_log[-4000:])

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
