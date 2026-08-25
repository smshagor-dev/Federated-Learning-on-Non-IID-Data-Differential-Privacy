from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fl_platform.v3.async_runtime import (
    AsyncModelState,
    AsyncUpdate,
    staleness_weight,
)
from fl_platform.v3.capabilities import CapabilityRequest, validate_capability_request
from fl_platform.v3.heterogeneity import (
    EdgeRequirements,
    eligible_for_edge_training,
    generate_profiles,
)
from fl_platform.v3.observability import RobustnessMetrics, RoundMetrics
from fl_platform.v3.release_gates import REQUIRED_V3_GATES, ReleaseGateReport
from fl_platform.v3.robust_aggregation import (
    coordinate_median,
    krum,
    multi_krum,
    trimmed_mean,
)
from fl_platform.v3.server_optimizers import AdaptiveServerOptimizer, OptimizerConfig
from fl_platform.v3.workloads import get_workload


def test_async_staleness_weight_reduces_old_updates() -> None:
    assert staleness_weight(0) == pytest.approx(1.0)
    assert staleness_weight(3) < staleness_weight(1)


def test_async_state_applies_and_rejects_overstale_update() -> None:
    state = AsyncModelState((0.0, 0.0), mixing_alpha=0.5, max_staleness=0)
    first = state.apply(AsyncUpdate("a", 0, (2.0, -2.0)))
    assert first.accepted
    assert state.model == pytest.approx((1.0, -1.0))
    stale = state.apply(AsyncUpdate("b", 0, (1.0, 1.0)))
    assert not stale.accepted
    assert stale.reason == "too stale"


def test_coordinate_median_and_trimmed_mean_ignore_extreme_outlier() -> None:
    updates = [
        (1.0, 1.0),
        (1.1, 0.9),
        (0.9, 1.1),
        (100.0, -100.0),
        (1.0, 1.0),
    ]
    assert coordinate_median(updates) == pytest.approx((1.0, 1.0))
    assert trimmed_mean(updates, trim_ratio=0.2) == pytest.approx(
        (1.0333333333, 0.9666666667)
    )


def test_krum_prefers_honest_cluster() -> None:
    updates = [
        (1.0, 1.0),
        (1.1, 1.0),
        (0.9, 1.0),
        (1.0, 0.9),
        (50.0, -50.0),
    ]
    selected = krum(updates, byzantine_clients=1)
    assert selected in updates[:4]
    aggregate = multi_krum(updates, byzantine_clients=1, select=2)
    assert aggregate[0] < 2.0
    assert aggregate[1] > 0.0


def test_capability_matrix_fails_closed() -> None:
    validate_capability_request(CapabilityRequest("fedavg", differential_privacy=True))
    with pytest.raises(ValueError, match="asynchronous fedavg"):
        validate_capability_request(CapabilityRequest("fedavg", asynchronous=True))
    with pytest.raises(ValueError, match="cannot inspect individual updates"):
        validate_capability_request(
            CapabilityRequest(
                "fedavg",
                secure_aggregation=True,
                robust_aggregation=True,
            )
        )
    with pytest.raises(ValueError, match="threshold"):
        validate_capability_request(
            CapabilityRequest(
                "fedavg",
                secure_aggregation=True,
                threshold_recovery=True,
            )
        )


@pytest.mark.parametrize("name", ["fedadam", "fedyogi", "fedadagrad"])
def test_adaptive_server_optimizers_produce_finite_update(name: str) -> None:
    optimizer = AdaptiveServerOptimizer(2, OptimizerConfig(name=name))
    update = optimizer.step((0.5, -0.25))
    assert len(update) == 2
    assert update[0] > 0.0
    assert update[1] < 0.0


def test_heterogeneity_generation_is_reproducible_and_edge_gated() -> None:
    first = generate_profiles(4, seed=7)
    second = generate_profiles(4, seed=7)
    assert first == second
    requirement = EdgeRequirements(min_memory_mb=256, min_cpu_cores=1)
    assert all(eligible_for_edge_training(profile, requirement) for profile in first)


def test_workload_catalog_does_not_overclaim_federated_native_loaders() -> None:
    assert get_workload("femnist").status == "loader-implemented-experimental"
    with pytest.raises(ValueError, match="not release-validated"):
        get_workload("femnist", require_validated=True)


def test_observability_records_communication_and_robustness() -> None:
    metrics = RoundMetrics(1, 10, 8, 2, 3.2, 0.2, 1000, 2000, 4.5)
    assert metrics.to_record()["communication_bytes"] == 3000
    robustness = RobustnessMetrics("label_flip", 2, 0.1, 0.9, 0.82)
    robustness.validate()
    assert robustness.accuracy_degradation == pytest.approx(0.08)


def test_release_gate_requires_all_thirteen_workstreams() -> None:
    report = ReleaseGateReport(dict.fromkeys(REQUIRED_V3_GATES, True))
    assert len(REQUIRED_V3_GATES) == 13
    assert report.release_ready()
    blocked = ReleaseGateReport({"async-runtime": True})
    assert not blocked.release_ready()
    with pytest.raises(RuntimeError, match="release blocked"):
        blocked.require_release_ready()


def test_lightweight_v3_modules_do_not_require_repo_root_packages() -> None:
    package_src = Path(__file__).resolve().parents[1] / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(package_src)
    command = [
        sys.executable,
        "-c",
        (
            "import sys; "
            "from fl_platform.v3.edge_runtime import Int8UpdateCodec; "
            "from fl_platform.v3.release_security import sha256_file; "
            "assert 'federated' not in sys.modules; "
            "assert 'federated.dp_accountant' not in sys.modules; "
            "assert Int8UpdateCodec is not None; "
            "assert sha256_file is not None"
        ),
    ]
    completed = subprocess.run(
        command,
        cwd=package_src,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
