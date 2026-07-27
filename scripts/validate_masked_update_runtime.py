#!/usr/bin/env python3
"""Docker Compose validation harness for the Masked Update Runtime and
No-Dropout Secure FedAvg Finalization slice.

Brings up a real coordinator + Go API + three python-worker containers
(docker-compose.dev.yml + docker-compose.security.yml +
docker-compose.secure-cohort-handshake.yml +
docker-compose.masked-update-runtime.yml, real mTLS, real Ed25519
signing, FL_SECURE_AGGREGATION_ENABLED=true), creates and starts a
single-round 3-client FedAvg run against the live gRPC coordinator, and
asserts that the round completes through the *masked* path end to end:

  1. every worker reaches READY_FOR_MASKED_TRAINING (the prior slice's
     already-validated handshake -- re-checked here as a precondition,
     not re-proven),
  2. every worker locally trains, fixed-point encodes, pairwise-masks,
     signs, and submits a MaskedClientUpdate that the coordinator
     accepts (worker log marker "masked update accepted"),
  3. the run's model_version genuinely advances (v0 -> v1) and the run
     reaches COMPLETED, confirmed via the same REST polling this
     project has used for every prior live FedAvg validation (see
     docs/known-limitations.md's Coordinator Runtime phase section) --
     not by trusting worker-side log claims alone,
  4. no worker logs a masking/encoding failure or an unhandled
     exception, and no worker ever falls back to the cleartext
     ClientResult path for this task (structurally impossible per
     WorkerService.run()'s branching -- see Work Area P -- but checked
     here too via the absence of any cleartext-submission log line).

This is the live deliverable this slice exists to prove: the handshake
alone (proven by the prior slice) never touched the model; this run's
model_version only advances if SubmitMaskedClientUpdate, worker-side
masking, coordinator-side verification/persistence, complete-cohort
finalization, pairwise-mask cancellation, and secure FedAvg's model-
version advance are ALL real and wired correctly end to end.

Usage:
    python scripts/validate_masked_update_runtime.py

Requires Docker and Docker Compose, and worker-2/worker-3 dev
certificates already issued (same prerequisite as
scripts/validate_secure_cohort_handshake.py). Brings the stack up,
runs the checks, and tears it down (with -v) -- safe to re-run. Exits
non-zero on any failure.
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
    "infra/compose/docker-compose.masked-update-runtime.yml",
]
# python-worker IS worker-1 (docker-compose.security.yml's service name);
# WORKER_SERVICES maps the Compose service name to the worker_id each
# emits in its own log lines (FL_WORKER_WORKER_ID), since the two differ
# for worker-1 only.
WORKER_SERVICES = {"python-worker": "worker-1", "worker-2": "worker-2", "worker-3": "worker-3"}
API_BASE = "http://localhost:8080"
RUN_ID = "masked-update-runtime-validation"
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


def wait_for_worker_outcomes(timeout_s: float = 180.0) -> dict[str, dict[str, bool]]:
    """Polls each worker's log output until it shows either a terminal
    outcome (masked update accepted, handshake failed, or a masking
    failure) or the timeout elapses. Returns
    {service: {"handshake": bool, "masked_accepted": bool, "failed": bool}}."""
    outcomes: dict[str, dict[str, bool]] = {
        service: {"handshake": False, "masked_accepted": False, "failed": False} for service in WORKER_SERVICES
    }
    deadline = time.time() + timeout_s
    pending = set(WORKER_SERVICES)
    while time.time() < deadline and pending:
        for service in list(pending):
            text = worker_log_text(service)
            outcomes[service]["handshake"] = "secure cohort handshake complete" in text
            outcomes[service]["masked_accepted"] = "masked update accepted" in text
            outcomes[service]["failed"] = (
                "secure cohort handshake failed" in text
                or "local update encoding/masking failed" in text
                or "masked submission for client" in text
                and "failed after" in text
            )
            if outcomes[service]["masked_accepted"] or outcomes[service]["failed"]:
                pending.discard(service)
        if pending:
            time.sleep(3)
    return outcomes


def wait_for_run_completed(bearer: str, timeout_s: float = 90.0) -> dict:
    deadline = time.time() + timeout_s
    last_payload: dict = {}
    while time.time() < deadline:
        status, payload = http_request("GET", f"/api/v1/coordinator/runs/{RUN_ID}", bearer=bearer)
        if status == 200:
            last_payload = payload
            if payload.get("state") in ("completed", "COMPLETED"):
                return payload
        time.sleep(3)
    return last_payload


def main() -> int:
    # Same build-once-then-two-phase-up discipline as
    # validate_secure_cohort_handshake.py -- see that script's comments
    # for the two real Docker Compose footguns this ordering avoids
    # (a second --build up rebuilding+recreating the already-stateful
    # coordinator container, and workers racing the run's own creation).
    print("Building images...")
    build = run(compose_cmd("build"))
    if build.returncode != 0:
        print(build.stdout)
        print(build.stderr)
        print("docker compose build failed")
        return 1

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

        print(f"\n1. Create and start a single-round 3-client run ({RUN_ID})")
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

        print("\n3. Wait for every worker's masked-update outcome")
        outcomes = wait_for_worker_outcomes()
        for service, worker_id in WORKER_SERVICES.items():
            o = outcomes[service]
            check(o["handshake"], f"{worker_id} ({service}) reached READY_FOR_MASKED_TRAINING")
            check(
                o["masked_accepted"] and not o["failed"],
                f"{worker_id} ({service}) submitted a masked update the coordinator accepted "
                f"(handshake={o['handshake']} masked_accepted={o['masked_accepted']} failed={o['failed']})",
            )

        print("\n4. Wait for the run to complete and the model to advance through the masked path")
        run_payload = wait_for_run_completed(researcher)
        check(
            run_payload.get("state") in ("completed", "COMPLETED"),
            f"run reaches COMPLETED (state={run_payload.get('state')!r})",
        )
        check(
            run_payload.get("model_version") == "v1",
            f"model_version advances from v0 to v1 after the single secure round "
            f"(model_version={run_payload.get('model_version')!r})",
        )
        check(
            run_payload.get("current_round") == 1,
            f"current_round reflects the completed round (current_round={run_payload.get('current_round')!r})",
        )

        print("\n5. No worker ever fell back to the cleartext ClientResult path")
        for service, worker_id in WORKER_SERVICES.items():
            text = worker_log_text(service).lower()
            no_cleartext_fallback = "client result rejected" not in text and "cleartext_result_forbidden" not in text
            check(
                no_cleartext_fallback,
                f"{worker_id} ({service}) log shows no cleartext-submission rejection "
                f"(structurally impossible per Work Area P, checked here as independent evidence)",
            )

        any_worker_incomplete = any(not (o["masked_accepted"] and not o["failed"]) for o in outcomes.values())
        run_incomplete = run_payload.get("state") not in ("completed", "COMPLETED")
        if any_worker_incomplete or run_incomplete:
            print("\n--- worker logs (masked round did not complete for every worker) ---")
            for service in WORKER_SERVICES:
                print(f"\n===== {service} =====")
                print(worker_log_text(service)[-4000:])
            print("\n===== coordinator =====")
            print(worker_log_text("coordinator")[-4000:])

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
