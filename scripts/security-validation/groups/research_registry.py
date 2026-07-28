"""Research registry live API validation.

This group exercises the real Go->Python write path over the Compose
stack. It validates route shape, permissions, durable creation/start/
cancel flow, and the combined reader/writer runtime-health projection.
"""

from __future__ import annotations

import time

from framework import Context, Scenario, Status


def _spec(experiment_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "experiment_name": "Runtime validation experiment",
        "research_question": "Does the live Go-to-Python command path remain durable?",
        "dataset": {
            "dataset_id": "cifar10",
            "dataset_version": "1.0",
            "dataset_checksum": "sha256:cifar10-demo",
            "split_seed": 7,
            "train_split_fraction": 0.8,
            "validation_split_fraction": 0.1,
            "test_split_fraction": 0.1,
            "preprocessing_configuration": {},
        },
        "partition": {
            "strategy": "dirichlet",
            "num_clients": 5,
            "seed": 11,
            "minimum_client_samples": 4,
            "alpha": 0.3,
            "classes_per_client": None,
            "quantity_skew_sigma": None,
            "partition_manifest_hash": "manifest-hash-123",
        },
        "model": {
            "model_id": "groupnorm_cnn",
            "model_version": "v1",
            "initialization_seed": 19,
        },
        "algorithm": {"algorithm_id": "fedavg", "parameters": {}},
        "privacy": {
            "privacy_mode": "user_level_dp",
            "noise_multiplier": 1.0,
            "target_delta": 1e-5,
            "user_level_clip_norm": 1.5,
            "sample_level_max_grad_norm": None,
            "epsilon_budget": None,
            "combined_epsilon": None,
            "client_weighting": "uniform",
        },
        "secure_aggregation": {
            "provider": "SECAGG_NO_DROPOUT_EXPERIMENTAL",
            "dropout_recovery_requested": False,
        },
        "adaptive_clipping": {
            "mode": "disabled",
            "initial_bound": None,
            "min_bound": None,
            "max_bound": None,
            "target_quantile": None,
            "learning_rate": None,
            "indicator_noise_multiplier": None,
        },
        "runtime": {
            "max_rounds": 3,
            "local_epochs": 1,
            "batch_size": 8,
            "learning_rate": 0.01,
            "evaluation_frequency": 1,
            "selected_clients_per_round": 3,
        },
        "seeds": {
            "seeds": [1, 2, 3],
            "partition_seed": 11,
            "worker_assignment_seed": 13,
            "coordinator_seed": 17,
        },
        "determinism_level": "STRICT_CPU",
        "tags": [],
        "creation_timestamp": "",
        "specification_hash": "",
    }


def _validate_create_start_cancel(ctx: Context) -> None:
    researcher = ctx.login("researcher@fl-platform.dev", "research-demo")
    experiment_id = f"expruntime{int(time.time())}"
    spec = _spec(experiment_id)
    status, body, _ = ctx.http(
        "POST",
        "/api/v1/research/experiments/validate",
        token=researcher,
        body={
            "specification": spec,
            "client_specification_hash": "",
            "correlation_id": "runtime-validate",
        },
    )
    ctx.assert_true(status == 200, f"validate returns 200, got {status}")
    ctx.assert_true(
        bool(body) and body.get("valid") is True,
        "validate reports valid=true",
    )

    idem_key = f"create-{experiment_id}"
    status, body, _ = ctx.http(
        "POST",
        "/api/v1/research/experiments",
        token=researcher,
        body={
            "specification": spec,
            "client_specification_hash": "",
            "idempotency_key": idem_key,
            "correlation_id": "runtime-create",
        },
        headers={"Idempotency-Key": idem_key},
    )
    ctx.assert_true(status == 201, f"create returns 201, got {status}")
    ctx.assert_true(
        bool(body) and body.get("experiment", {}).get("experiment_id") == experiment_id,
        "create response contains the durable experiment id",
    )

    status, detail, _ = ctx.http(
        "GET", f"/api/v1/research/experiments/{experiment_id}", token=researcher
    )
    ctx.assert_true(status == 200, f"detail returns 200 after create, got {status}")
    ctx.assert_true(
        bool(detail) and detail.get("current_state") == "READY",
        "detail sees the Python-authored READY state immediately",
    )

    start_key = f"start-{experiment_id}"
    status, body, _ = ctx.http(
        "POST",
        f"/api/v1/research/experiments/{experiment_id}/start",
        token=researcher,
        body={
            "execution_mode": "SYNTHETIC_TEST_EXECUTION",
            "idempotency_key": start_key,
            "correlation_id": "runtime-start",
        },
        headers={"Idempotency-Key": start_key},
    )
    ctx.assert_true(status == 200, f"start returns 200, got {status}")
    ctx.assert_true(
        bool(body) and body.get("execution_mode") == "SYNTHETIC_TEST_EXECUTION",
        "start is explicitly labeled synthetic test execution",
    )

    status, metrics, _ = ctx.http(
        "GET", f"/api/v1/research/experiments/{experiment_id}/metrics", token=researcher
    )
    ctx.assert_true(status == 200, f"metrics returns 200 after start, got {status}")
    ctx.assert_true(
        bool(metrics) and isinstance(metrics.get("metrics"), list),
        "metrics endpoint exposes the synthetic metric journal",
    )

    cancel_key = f"cancel-{experiment_id}"
    status, body, _ = ctx.http(
        "POST",
        f"/api/v1/research/experiments/{experiment_id}/cancel",
        token=researcher,
        body={
            "reason": "runtime harness cancellation replay check",
            "idempotency_key": cancel_key,
            "correlation_id": "runtime-cancel",
        },
        headers={"Idempotency-Key": cancel_key},
    )
    ctx.assert_true(status == 200, f"cancel returns 200, got {status}")
    ctx.assert_true(
        bool(body) and "current_state" in body,
        "cancel returns a stable current-state response",
    )


def _viewer_and_service_permissions(ctx: Context) -> None:
    viewer = ctx.login("viewer@fl-platform.dev", "viewer-demo")
    service = ctx.login("service@fl-platform.dev", "service-demo")
    status, _, _ = ctx.http(
        "POST",
        "/api/v1/research/experiments/validate",
        token=viewer,
        body={"specification": _spec("expdeny001"), "client_specification_hash": ""},
    )
    ctx.assert_true(status == 403, f"viewer validate is denied with 403, got {status}")
    status, _, _ = ctx.http("GET", "/api/v1/research/runtime/health", token=service)
    ctx.assert_true(
        status == 403,
        f"SERVICE public health access remains denied with 403, got {status}",
    )


def _runtime_health_reports_reader_and_writer(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/research/runtime/health", token=admin)
    ctx.assert_true(status == 200, f"runtime health returns 200, got {status}")
    ctx.assert_true(
        bool(body) and "reader" in body and "writer" in body,
        "health response includes both reader and writer sections",
    )
    ctx.assert_true(
        bool(body) and body.get("writes_available") is True,
        "health reports writes available when the Python writer is live",
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="research-registry.validate-create-start-cancel",
        name="Research registry live write flow stays Python-authoritative and durable",
        category="research-registry",
        description=(
            "Validate, create, read, start synthetic execution, read metrics, "
            "and cancel via the public Go API."
        ),
        required_services=("research-writer", "api"),
        prerequisites="stack up",
        assertion=(
            "public write routes return durable results and immediate read visibility"
        ),
        expected_result=(
            "validate=200, create=201, read=200, start=200, metrics=200, cancel=200"
        ),
        timeout_seconds=45.0,
        cleanup="none; unique experiment ids are used",
        required=True,
        support_status=Status.SKIPPED,
        run=_validate_create_start_cancel,
    ),
    Scenario(
        scenario_id="research-registry.permissions",
        name="Research registry mutation routes enforce viewer/service restrictions",
        category="research-registry",
        description=(
            "VIEWER cannot validate; SERVICE still has no implicit public access."
        ),
        required_services=("research-writer", "api"),
        prerequisites="stack up",
        assertion="viewer validate => 403, service health => 403",
        expected_result="both denied",
        timeout_seconds=20.0,
        cleanup="none",
        required=True,
        support_status=Status.SKIPPED,
        run=_viewer_and_service_permissions,
    ),
    Scenario(
        scenario_id="research-registry.runtime-health",
        name="Research runtime health combines reader and live writer status",
        category="research-registry",
        description=(
            "GET /api/v1/research/runtime/health exposes both reader and "
            "writer health when the writer is live."
        ),
        required_services=("research-writer", "api"),
        prerequisites="stack up",
        assertion="health includes reader and writer and reports writes_available=true",
        expected_result="combined health present",
        timeout_seconds=20.0,
        cleanup="none",
        required=True,
        support_status=Status.SKIPPED,
        run=_runtime_health_reports_reader_and_writer,
    ),
]
