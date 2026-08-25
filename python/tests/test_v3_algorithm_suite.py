from __future__ import annotations

import pytest

from fl_platform.v3.algorithm_suite import (
    fedbn_partition,
    fednova_aggregate,
    fedrep_partition,
    moon_contrastive_loss,
    pfedme_personalized_step,
)
from fl_platform.v3.runtime_integration import AggregationConfig, V3AggregationEngine
from fl_platform.v3.server_optimizers import OptimizerConfig
from fl_platform.workers import TrainingResult


def _result(
    client_id: str,
    update: tuple[float, ...],
    *,
    samples: int = 10,
    steps: int = 1,
) -> TrainingResult:
    return TrainingResult(
        run_id="run-v3",
        round_id=1,
        client_id=client_id,
        model_version="model-1",
        sample_count=samples,
        local_step_count=steps,
        model_update=update,
    )


def test_fednova_equal_local_steps_reduce_to_weighted_mean() -> None:
    aggregate = fednova_aggregate(
        ((1.0, 2.0), (4.0, 8.0)),
        local_steps=(2, 2),
        weights=(1.0, 1.0),
    )
    assert aggregate == pytest.approx((2.5, 5.0))


def test_fednova_normalizes_heterogeneous_local_work() -> None:
    aggregate = fednova_aggregate(
        ((1.0, 2.0), (4.0, 8.0)),
        local_steps=(1, 2),
        weights=(1.0, 1.0),
    )
    assert aggregate == pytest.approx((2.25, 4.5))
    assert aggregate != pytest.approx((2.5, 5.0))


def test_runtime_fednova_consumes_worker_local_step_counts() -> None:
    engine = V3AggregationEngine(
        2,
        AggregationConfig(
            algorithm="fednova",
            strategy="mean",
            weighting="uniform",
        ),
    )
    outcome = engine.aggregate(
        [
            _result("a", (1.0, 2.0), steps=1),
            _result("b", (4.0, 8.0), steps=2),
        ]
    )
    assert outcome.update == pytest.approx((2.25, 4.5))
    assert outcome.client_count == 2


def test_runtime_fednova_rejects_unvalidated_compositions() -> None:
    with pytest.raises(ValueError, match="robust"):
        V3AggregationEngine(
            2,
            AggregationConfig(
                algorithm="fednova",
                strategy="median",
                weighting="uniform",
            ),
        )
    with pytest.raises(ValueError, match="adaptive server optimizer"):
        V3AggregationEngine(
            2,
            AggregationConfig(
                algorithm="fednova",
                optimizer=OptimizerConfig(name="fedadam"),
            ),
        )
    with pytest.raises(ValueError, match="secure aggregation"):
        V3AggregationEngine(
            2,
            AggregationConfig(
                algorithm="fednova",
                secure_aggregation=True,
            ),
        )


def test_fednova_rejects_zero_local_steps() -> None:
    with pytest.raises(ValueError, match="local_steps"):
        fednova_aggregate(
            ((1.0,), (2.0,)),
            local_steps=(1, 0),
            weights=(1.0, 1.0),
        )


def test_fedbn_keeps_batch_norm_state_personalized() -> None:
    partition = fedbn_partition(
        {
            "encoder.weight": 1.0,
            "bn.weight": 2.0,
            "bn.bias": 3.0,
        },
        batch_norm_names=frozenset({"bn.weight", "bn.bias"}),
    )
    assert partition.shared == {"encoder.weight": 1.0}
    assert partition.personalized == {"bn.weight": 2.0, "bn.bias": 3.0}


def test_fedrep_requires_exact_representation_head_partition() -> None:
    partition = fedrep_partition(
        {"rep.0": 1.0, "rep.1": 2.0, "head": 3.0},
        representation_names=frozenset({"rep.0", "rep.1"}),
        head_names=frozenset({"head"}),
    )
    assert partition.shared == {"rep.0": 1.0, "rep.1": 2.0}
    assert partition.personalized == {"head": 3.0}

    with pytest.raises(ValueError, match="cover state exactly"):
        fedrep_partition(
            {"rep": 1.0, "head": 2.0, "extra": 3.0},
            representation_names=frozenset({"rep"}),
            head_names=frozenset({"head"}),
        )


def test_moon_loss_prefers_global_alignment_over_previous_local_model() -> None:
    aligned = moon_contrastive_loss(
        (1.0, 0.0),
        (1.0, 0.0),
        (0.0, 1.0),
        temperature=0.5,
    )
    misaligned = moon_contrastive_loss(
        (1.0, 0.0),
        (0.0, 1.0),
        (1.0, 0.0),
        temperature=0.5,
    )
    assert aligned < misaligned
    assert aligned == pytest.approx(0.126928, rel=1e-5)


def test_pfedme_personalized_step_applies_proximal_pull() -> None:
    updated = pfedme_personalized_step(
        (1.0, 2.0),
        (0.0, 0.0),
        (0.5, -0.5),
        learning_rate=0.1,
        proximal_lambda=2.0,
    )
    assert updated == pytest.approx((0.75, 1.65))
