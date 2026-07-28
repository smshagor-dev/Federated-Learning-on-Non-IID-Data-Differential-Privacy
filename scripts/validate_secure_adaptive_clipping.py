#!/usr/bin/env python3
"""Docker Compose validation harness for the Secure Adaptive Clipping
with Private Indicator Aggregation slice.

Brings up a real coordinator + Go API + three python-worker containers
(docker-compose.dev.yml + docker-compose.security.yml +
docker-compose.secure-cohort-handshake.yml +
docker-compose.secure-adaptive-clipping.yml, real mTLS, real Ed25519
signing, FL_SECURE_AGGREGATION_ENABLED=true), creates and starts a
single-round 3-client FedAvg run with privacy.mode=user_level_dp and
adaptive clipping enabled, and asserts the round completes through the
secure adaptive-clipping path end to end:

  1. every worker reaches READY_FOR_MASKED_TRAINING,
  2. every worker applies real worker-side user-level clipping and
     submits a masked update the coordinator accepts,
  3. the coordinator emits secure adaptive-clipping security events
     proving the signed adaptive binding was accepted, the complete
     cohort indicator count was reconstructed, and the next clip state
     was published exactly once,
  4. the run reaches COMPLETED and the model advances (v0 -> v1),
  5. no worker falls back to the cleartext ClientResult path,
  6. the coordinator's own structured log shows the ordinary secure
     round lifecycle completing,
  7. the existing user-level privacy health/budget routes still report
     the user-level half of the same real run, while the coordinator's
     run-detail route reflects adaptive clipping being enabled.

This does not and cannot prove the exact private indicator value each
worker produced or the exact noisy over-threshold count used inside the
adaptive step: those are intentionally not exposed over the public API.
What this script proves is that the real live multi-container wiring
for signed adaptive bindings, masked indicator carriage, coordinator-
side complete-cohort reconstruction, adaptive clip-state advancement,
and ordinary secure FedAvg completion is working end to end.
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
    "infra/compose/docker-compose.secure-adaptive-clipping.yml",
]
WORKER_SERVICES = {"python-worker": "worker-1", "worker-2": "worker-2", "worker-3": "worker-3"}
API_BASE = "http://localhost:8080"
RUN_ID = "secure-adaptive-clipping-validation"
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
    for compose_file in COMPOSE_FILES:
        cmd += ["-f", compose_file]
    cmd += list(args)
    return cmd


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )


def http_request(
    method: str, path: str, bearer: str = "", body: dict | None = None
) -> tuple[int, dict]:
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


def log_text(service: str) -> str:
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
            text = log_text(service)
            outcomes[service]["handshake"] = "secure cohort handshake complete" in text
            outcomes[service]["clipped"] = "secure user-level DP clipping applied" in text
            outcomes[service]["masked_accepted"] = "masked update accepted" in text
            outcomes[service]["failed"] = (
                "secure cohort handshake failed" in text
                or "local update encoding/masking failed" in text
                or ("masked submission for client" in text and "failed after" in text)
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


def wait_for_security_events(bearer: str, expected: set[str], timeout_s: float = 30.0) -> set[str]:
    deadline = time.time() + timeout_s
    event_types: set[str] = set()
    while time.time() < deadline:
        status, payload = http_request("GET", "/api/v1/security/events?limit=200", bearer=bearer)
        if status == 200:
            event_types = {event.get("event_type") for event in payload.get("events", [])}
            if expected.issubset(event_types):
                return event_types
        time.sleep(2)
    return event_types


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

    coordinator_log = ""
    try:
        print("Waiting for health...")
        wait_for_healthy()

        researcher = login("researcher@fl-platform.dev", "research-demo")

        print(f"\n1. Create and start a single-round 3-client secure adaptive-clipping run ({RUN_ID})")
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
                        "initial_clipping_bound": 0.01,
                        "weighting_strategy": "uniform",
                        "secure_random": True,
                        "epsilon_budget": 0.0,
                    },
                    "adaptive_clipping": {
                        "enabled": True,
                        "target_quantile": 0.5,
                        "clip_learning_rate": 0.5,
                        "initial_clip": 0.01,
                        "min_clip": 0.001,
                        "max_clip": 1.0,
                        "count_noise_multiplier": 1.0,
                        "target_delta": 1e-5,
                    },
                },
            },
        )
        check(status == 201, f"create adaptive-clipping run returns 201, got {status}: {payload}")
        status, payload = http_request("POST", f"/api/v1/coordinator/runs/{RUN_ID}/start", bearer=researcher)
        check(status == 200, f"start adaptive-clipping run returns 200, got {status}: {payload}")

        print(f"\n2. Bring up workers: {', '.join(WORKER_SERVICES)}")
        up_workers = run(compose_cmd("up", "-d", *WORKER_SERVICES))
        check(up_workers.returncode == 0, "docker compose up (workers) succeeded")
        if up_workers.returncode != 0:
            print(up_workers.stdout)
            print(up_workers.stderr)

        print("\n3. Wait for every worker's secure adaptive-clipping outcome")
        outcomes = wait_for_worker_outcomes()
        for service, worker_id in WORKER_SERVICES.items():
            outcome = outcomes[service]
            check(outcome["handshake"], f"{worker_id} ({service}) reached READY_FOR_MASKED_TRAINING")
            check(
                outcome["clipped"],
                f"{worker_id} ({service}) applied real worker-side clipping before adaptive-indicator submission",
            )
            check(
                outcome["masked_accepted"] and not outcome["failed"],
                f"{worker_id} ({service}) submitted a masked adaptive-clipping update the coordinator accepted "
                f"(handshake={outcome['handshake']} clipped={outcome['clipped']} "
                f"masked_accepted={outcome['masked_accepted']} failed={outcome['failed']})",
            )

        print("\n4. Wait for secure adaptive-clipping security events")
        expected_events = {
            "SECURE_ADAPTIVE_CLIPPING_CONFIGURATION_ACCEPTED",
            "SECURE_ADAPTIVE_CLIPPING_INDICATOR_ACCEPTED",
            "SECURE_ADAPTIVE_CLIPPING_COMPLETE_COHORT_RECONSTRUCTED",
            "SECURE_ADAPTIVE_CLIPPING_NEXT_STATE_PUBLISHED",
            "SECURE_ADAPTIVE_CLIPPING_ROUND_COMPLETED",
        }
        event_types = wait_for_security_events(researcher, expected_events)
        for event_name in sorted(expected_events):
            check(event_name in event_types, f"security-event journal contains a real {event_name} event")
        check(
            "SECURE_ADAPTIVE_CLIPPING_CONFIGURATION_REJECTED" not in event_types,
            "no spurious SECURE_ADAPTIVE_CLIPPING_CONFIGURATION_REJECTED event for this valid config",
        )
        check(
            "SECURE_ADAPTIVE_CLIPPING_INDICATOR_REJECTED" not in event_types,
            "no SECURE_ADAPTIVE_CLIPPING_INDICATOR_REJECTED event for accepted worker submissions",
        )
        check(
            "SECURE_ADAPTIVE_CLIPPING_ROUND_ABORTED" not in event_types,
            "no SECURE_ADAPTIVE_CLIPPING_ROUND_ABORTED event for this completed round",
        )

        print("\n5. Wait for the run to complete and the model to advance")
        run_payload = wait_for_run_completed(researcher)
        check(
            run_payload.get("state") in ("completed", "COMPLETED"),
            f"run reaches COMPLETED (state={run_payload.get('state')!r})",
        )
        check(
            run_payload.get("model_version") == "v1",
            f"model_version advances from v0 to v1 after the secure adaptive-clipping round "
            f"(model_version={run_payload.get('model_version')!r})",
        )
        check(
            run_payload.get("current_round") == 1,
            f"current_round reflects the completed round (current_round={run_payload.get('current_round')!r})",
        )
        print("\n6. No worker ever fell back to the cleartext ClientResult path")
        for service, worker_id in WORKER_SERVICES.items():
            text = log_text(service).lower()
            no_cleartext_fallback = "client result rejected" not in text and "cleartext_result_forbidden" not in text
            check(no_cleartext_fallback, f"{worker_id} ({service}) log shows no cleartext-submission rejection")

        print("\n7. Coordinator structured log confirms the ordinary secure round-completion sequence")
        coordinator_log = log_text("coordinator")
        for marker in (
            "AGGREGATION_COMPLETED",
            "MODEL_VERSION_UPDATED",
            "CHECKPOINT_COMPLETED",
            "RUN_COMPLETED",
        ):
            check(marker in coordinator_log, f"coordinator structured log contains event_type={marker}")

        print("\n8. Privacy metrics/ledger/projection expose the adaptive-clipping half of this same real run")
        status, metrics_payload = http_request(
            "GET", f"/api/v1/coordinator/runs/{RUN_ID}/privacy/metrics", bearer=researcher
        )
        check(status == 200, f"GET .../privacy/metrics returns 200, got {status}")
        check(
            metrics_payload.get("has_clipping") is True,
            f"privacy metrics report has_clipping=true, got {metrics_payload.get('has_clipping')!r}",
        )
        check(
            isinstance(metrics_payload.get("current_clip_value"), (int, float))
            and metrics_payload["current_clip_value"] > 0,
            f"privacy metrics report a positive current_clip_value, got {metrics_payload.get('current_clip_value')!r}",
        )

        status, ledger_payload = http_request(
            "GET", f"/api/v1/coordinator/runs/{RUN_ID}/privacy/ledger", bearer=researcher
        )
        check(status == 200, f"GET .../privacy/ledger returns 200, got {status}")
        clipping_entries = ledger_payload.get("clipping_entries", [])
        check(
            len(clipping_entries) == 1,
            f"privacy ledger contains exactly 1 adaptive-clipping entry, got {len(clipping_entries)}",
        )
        if clipping_entries:
            check(
                clipping_entries[0].get("round_id") == 1,
                f"adaptive-clipping ledger entry is for round 1, got {clipping_entries[0].get('round_id')!r}",
            )

        status, projection_payload = http_request(
            "GET", f"/api/v1/coordinator/runs/{RUN_ID}/privacy/projection", bearer=researcher
        )
        check(status == 200, f"GET .../privacy/projection returns 200, got {status}")
        check(
            projection_payload.get("has_clipping") is True,
            f"privacy projection reports has_clipping=true, got {projection_payload.get('has_clipping')!r}",
        )

        print("\n9. Existing user-level privacy routes still reflect this real adaptive-clipping run")
        status, health_payload = http_request(
            "GET", "/api/v1/secure-aggregation/privacy/health", bearer=researcher
        )
        check(status == 200, f"GET .../privacy/health returns 200, got {status}")
        check(
            health_payload.get("active_runs_with_user_level_dp", 0) >= 1,
            "health reports at least one active run with a user-level-DP layer (this run)",
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

        status, status_payload = http_request(
            "GET", "/api/v1/secure-aggregation/privacy/status", bearer=researcher
        )
        check(status == 200, f"GET .../privacy/status returns 200, got {status}")
        check(
            status_payload.get("provider") == "SECAGG_NO_DROPOUT_EXPERIMENTAL",
            f"status.provider is the real, unchanged provider name, got {status_payload.get('provider')!r}",
        )

        any_worker_incomplete = any(
            not (outcome["masked_accepted"] and not outcome["failed"] and outcome["clipped"])
            for outcome in outcomes.values()
        )
        run_incomplete = run_payload.get("state") not in ("completed", "COMPLETED")
        if any_worker_incomplete or run_incomplete:
            print("\n--- worker logs (secure adaptive-clipping round did not complete for every worker) ---")
            for service in WORKER_SERVICES:
                print(f"\n===== {service} =====")
                print(log_text(service)[-4000:])
            print("\n===== coordinator =====")
            print(coordinator_log[-4000:])

    finally:
        print("\nTearing down...")
        run(compose_cmd("down", "-v"))
        print("10. Verifying teardown left no project containers running")
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
