#!/usr/bin/env python3
"""Docker Compose validation harness for the Secure User-Level
Differential Privacy Runtime slice.

Brings up a real coordinator + Go API + three python-worker containers
(docker-compose.dev.yml + docker-compose.security.yml +
docker-compose.secure-cohort-handshake.yml +
docker-compose.secure-user-level-dp.yml, real mTLS, real Ed25519
signing, FL_SECURE_AGGREGATION_ENABLED=true), creates and starts a
single-round 3-client FedAvg run with privacy.mode=user_level_dp and a
deliberately very small clip_norm (0.01 -- real PyTorch gradients on
even a tiny toy model virtually always exceed this, so clipping is
exercised by ordinary real training, not a synthetic injected value),
and asserts the round completes through the secure user-level-DP path
end to end:

  1. every worker reaches READY_FOR_MASKED_TRAINING,
  2. every worker logs applying real worker-side global L2 clipping
     (the "secure user-level DP clipping applied" marker) -- proving
     clipping actually engaged, not merely that the wiring exists,
  3. every worker's masked update (carrying a signed
     SignedUserLevelPrivacyAttestation) is accepted by the coordinator,
  4. the run's model_version genuinely advances (v0 -> v1) and the run
     reaches COMPLETED, confirmed via REST polling,
  5. no worker ever falls back to the cleartext ClientResult path,
  6. the coordinator's own structured event log shows the round
     completing through the ordinary AGGREGATION_COMPLETED ->
     MODEL_VERSION_UPDATED -> RUN_COMPLETED sequence (the same bridge
     apply_secure_aggregate_and_advance already uses for every secure
     round -- this slice adds the accountant/ledger commit inside that
     same call, not a new code path).

This does not and cannot prove the exact numeric noise value added
(the production run uses the real OS-CSPRNG-backed
CryptoSecureNoiseProvider, not a deterministic test provider -- see
docs/secure-user-level-dp-semantics.md's noise placement section and
secure_aggregation_session_manager_test.cpp for the deterministic-
noise-engine proof that noise is added exactly once, correctly placed,
and changes the result). What this script proves is that the real,
live, multi-container pipeline -- worker-side clipping, signed
attestation construction and verification, coordinator-side masked
aggregation, central noise injection, and secure FedAvg's model-
version advance -- is wired correctly end to end, not just each piece
in isolation.

Usage:
    python scripts/validate_secure_user_level_dp.py

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
    "infra/compose/docker-compose.secure-user-level-dp.yml",
]
WORKER_SERVICES = {"python-worker": "worker-1", "worker-2": "worker-2", "worker-3": "worker-3"}
API_BASE = "http://localhost:8080"
RUN_ID = "secure-user-level-dp-validation"
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
    outcomes: dict[str, dict[str, bool]] = {
        service: {
            "handshake": False,
            "clipped": False,
            "masked_accepted": False,
            "failed": False,
        }
        for service in WORKER_SERVICES
    }
    deadline = time.time() + timeout_s
    pending = set(WORKER_SERVICES)
    while time.time() < deadline and pending:
        for service in list(pending):
            text = worker_log_text(service)
            outcomes[service]["handshake"] = "secure cohort handshake complete" in text
            outcomes[service]["clipped"] = "secure user-level DP clipping applied" in text
            outcomes[service]["masked_accepted"] = "masked update accepted" in text
            failed = (
                "secure cohort handshake failed" in text
                or "local update encoding/masking failed" in text
                or ("masked submission for client" in text and "failed after" in text)
            )
            outcomes[service]["failed"] = failed
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

        print(f"\n1. Create and start a single-round 3-client secure user-level-DP run ({RUN_ID})")
        status, payload = http_request(
            "POST",
            "/api/v1/coordinator/runs",
            bearer=researcher,
            body={
                "run_id": RUN_ID,
                "algorithm": "fedavg",
                "weighting": "uniform",
                "total_clients": 3,
                "target_clients_per_round": 3,
                "max_rounds": 1,
                "minimum_valid_results": 1,
                "client_ids": ["worker-1", "worker-2", "worker-3"],
                "local_epochs": 1,
                "batch_size": 8,
                "learning_rate": 0.01,
                "task_lease_seconds": 90,
                "privacy": {
                    "mode": "user_level_dp",
                    "user_level": {
                        "noise_multiplier": 1.0,
                        "target_delta": 1e-5,
                        "accountant": "rdp",
                        # Deliberately tiny -- real gradients on even a
                        # small toy model virtually always exceed this,
                        # so clipping engages via ordinary real
                        # training, not a synthetic injected value.
                        "initial_clipping_bound": 0.01,
                        "weighting_strategy": "uniform",
                        "secure_random": True,
                        "epsilon_budget": 0.0,
                    },
                },
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

        print("\n3. Wait for every worker's secure user-level-DP outcome")
        outcomes = wait_for_worker_outcomes()
        for service, worker_id in WORKER_SERVICES.items():
            o = outcomes[service]
            check(o["handshake"], f"{worker_id} ({service}) reached READY_FOR_MASKED_TRAINING")
            check(
                o["clipped"],
                f"{worker_id} ({service}) applied real worker-side global L2 clipping "
                f"(the extremely tight clip_norm=0.01 engaged on real training gradients)",
            )
            check(
                o["masked_accepted"] and not o["failed"],
                f"{worker_id} ({service}) submitted a signed, attested masked update the "
                f"coordinator accepted (handshake={o['handshake']} clipped={o['clipped']} "
                f"masked_accepted={o['masked_accepted']} failed={o['failed']})",
            )

        print("\n4. Wait for the run to complete and the model to advance through the secure user-level-DP path")
        run_payload = wait_for_run_completed(researcher)
        check(
            run_payload.get("state") in ("completed", "COMPLETED"),
            f"run reaches COMPLETED (state={run_payload.get('state')!r})",
        )
        check(
            run_payload.get("model_version") == "v1",
            f"model_version advances from v0 to v1 after the single secure private round "
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
                f"{worker_id} ({service}) log shows no cleartext-submission rejection",
            )

        print("\n6. Coordinator's own structured log confirms the ordinary round-completion sequence")
        coordinator_log = worker_log_text("coordinator")
        for marker in (
            "AGGREGATION_COMPLETED",
            "MODEL_VERSION_UPDATED",
            "CHECKPOINT_COMPLETED",
            "RUN_COMPLETED",
        ):
            check(
                marker in coordinator_log,
                f"coordinator structured log contains event_type={marker}",
            )

        # Secure User-Level DP Operations, Observability, and Release
        # Evidence slice, Work Area U: bounded extension -- events,
        # metrics, and the new read-only API surface, against this same
        # real completed run. Not a re-run of the mechanism itself
        # (checks 1-6 above already proved that); this section proves
        # the *observability* this slice adds around it is real.
        print("\n7. Coordinator's security-event journal contains the new SECURE_USER_LEVEL_DP_* events")
        # SECURE_USER_LEVEL_DP_CLIPPING_APPLIED is worker-emitted: it goes
        # into the worker's own local journal first, then reaches the
        # coordinator only via the existing worker-security-event-
        # centralization batch flush (FL_WORKER_SECURITY_EVENT_FLUSH_INTERVAL_SECONDS,
        # 5s in this stack -- see docker-compose.security.yml). The other
        # six SECURE_USER_LEVEL_DP_* events are coordinator-emitted,
        # in-process, immediate. Polled with a bounded retry loop (not a
        # single point-in-time check) specifically because of that real,
        # disclosed cross-service flush latency -- found live: the first
        # version of this check read the journal exactly once and missed
        # CLIPPING_APPLIED because the flush had not yet run.
        deadline = time.time() + 20.0
        event_types: set[str] = set()
        status = 0
        while time.time() < deadline:
            status, events_payload = http_request(
                "GET", "/api/v1/security/events?limit=200", bearer=researcher
            )
            event_types = {event.get("event_type") for event in events_payload.get("events", [])}
            if "SECURE_USER_LEVEL_DP_CLIPPING_APPLIED" in event_types:
                break
            time.sleep(2)
        check(status == 200, f"GET /api/v1/security/events returns 200, got {status}")
        for expected in (
            "SECURE_USER_LEVEL_DP_CONFIGURATION_ACCEPTED",
            "SECURE_USER_LEVEL_DP_BUDGET_RESERVED",
            "SECURE_USER_LEVEL_DP_CLIPPING_APPLIED",
            "SECURE_USER_LEVEL_DP_ATTESTATION_ACCEPTED",
            "SECURE_USER_LEVEL_DP_NOISE_APPLIED",
            "SECURE_USER_LEVEL_DP_ACCOUNTING_COMMITTED",
            "SECURE_USER_LEVEL_DP_ROUND_COMPLETED",
        ):
            check(expected in event_types, f"security-event journal contains a real {expected} event")

        print("\n8. New secure-aggregation privacy API surface reflects this real run")
        status, status_payload = http_request(
            "GET", "/api/v1/secure-aggregation/privacy/status", bearer=researcher
        )
        check(status == 200, f"GET .../privacy/status returns 200, got {status}")
        check(
            status_payload.get("provider") == "SECAGG_NO_DROPOUT_EXPERIMENTAL",
            f"status.provider is the real, unchanged provider name, got {status_payload.get('provider')!r}",
        )

        status, health_payload = http_request(
            "GET", "/api/v1/secure-aggregation/privacy/health", bearer=researcher
        )
        check(status == 200, f"GET .../privacy/health returns 200, got {status}")
        check(
            health_payload.get("active_runs_with_user_level_dp", 0) >= 1,
            "health reports at least one active user-level-DP run (this run)",
        )
        check(
            bool(health_payload.get("last_successful_round_at")),
            "health reports a non-empty last_successful_round_at after this run's real round",
        )

        status, budget_payload = http_request(
            "GET", f"/api/v1/secure-aggregation/privacy/budget?run_id={RUN_ID}", bearer=researcher
        )
        check(status == 200, f"GET .../privacy/budget returns 200, got {status}")
        check(
            budget_payload.get("rounds_committed") == 1,
            f"budget reports exactly 1 committed round for this run, got {budget_payload.get('rounds_committed')!r}",
        )
        check(
            isinstance(budget_payload.get("epsilon_spent"), (int, float)) and budget_payload["epsilon_spent"] > 0,
            f"budget reports a real positive epsilon_spent, got {budget_payload.get('epsilon_spent')!r}",
        )

        status, rounds_payload = http_request(
            "GET", f"/api/v1/secure-aggregation/privacy/rounds?run_id={RUN_ID}", bearer=researcher
        )
        check(status == 200, f"GET .../privacy/rounds returns 200, got {status}")
        rounds = rounds_payload.get("rounds", [])
        check(len(rounds) == 1 and rounds[0].get("round_id") == 1, f"rounds list contains exactly round 1, got {rounds!r}")

        status, round_payload = http_request(
            "GET", f"/api/v1/secure-aggregation/privacy/rounds/1?run_id={RUN_ID}", bearer=researcher
        )
        check(status == 200, f"GET .../privacy/rounds/1 returns 200, got {status}")
        check(
            round_payload.get("num_clients") == 3,
            f"round detail reports the real 3-client cohort size, got {round_payload.get('num_clients')!r}",
        )

        status, missing_round_payload = http_request(
            "GET", f"/api/v1/secure-aggregation/privacy/rounds/999?run_id={RUN_ID}", bearer=researcher
        )
        check(status == 404, f"a round_id that was never committed returns 404, got {status}: {missing_round_payload}")

        print("\n9. Role-based access control: VIEWER denied per-run detail, SERVICE denied everything")
        viewer = login("viewer@fl-platform.dev", "viewer-demo")
        status, _ = http_request("GET", "/api/v1/secure-aggregation/privacy/status", bearer=viewer)
        check(status == 200, f"VIEWER can read aggregate status, got {status}")
        status, _ = http_request("GET", f"/api/v1/secure-aggregation/privacy/budget?run_id={RUN_ID}", bearer=viewer)
        check(status == 403, f"VIEWER is denied per-run budget detail, got {status}")

        service_token = login("service@fl-platform.dev", "service-demo")
        status, _ = http_request("GET", "/api/v1/secure-aggregation/privacy/health", bearer=service_token)
        check(status == 403, f"SERVICE role has no implicit access to the health route, got {status}")

        print("\n10. Prometheus metrics surface the new fl_secure_user_dp_* series")
        metrics_response = run(["curl", "-s", f"{API_BASE}/metrics"])
        metrics_text = metrics_response.stdout
        check(
            "fl_secure_user_dp_route_requests_total" in metrics_text,
            "GET /metrics includes fl_secure_user_dp_route_requests_total",
        )
        check(
            "fl_secure_user_dp_active_runs" in metrics_text,
            "GET /metrics includes fl_secure_user_dp_active_runs",
        )

        any_worker_incomplete = any(
            not (o["masked_accepted"] and not o["failed"] and o["clipped"]) for o in outcomes.values()
        )
        run_incomplete = run_payload.get("state") not in ("completed", "COMPLETED")
        if any_worker_incomplete or run_incomplete:
            print("\n--- worker logs (secure user-level-DP round did not complete for every worker) ---")
            for service in WORKER_SERVICES:
                print(f"\n===== {service} =====")
                print(worker_log_text(service)[-4000:])
            print("\n===== coordinator =====")
            print(coordinator_log[-4000:])

    finally:
        print("\nTearing down...")
        run(compose_cmd("down", "-v"))
        print("11. Verifying teardown left no project containers running")
        remaining = run(compose_cmd("ps", "--format", "json"))
        remaining_lines = [line for line in remaining.stdout.splitlines() if line.strip()]
        check(len(remaining_lines) == 0, f"no project containers remain after teardown, got {len(remaining_lines)}")

    print(f"\n{_checks_passed} passed, {len(_checks_failed)} failed")
    if _checks_failed:
        print("Failed checks:")
        for description in _checks_failed:
            print(f"  - {description}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
