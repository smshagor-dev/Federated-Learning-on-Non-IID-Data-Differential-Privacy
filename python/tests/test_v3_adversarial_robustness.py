from __future__ import annotations

import pytest

from fl_platform.v3.adversarial_benchmark import (
    RobustnessTrialConfig,
    run_robustness_benchmark,
    run_robustness_trial,
)
from fl_platform.v3.attacks import (
    AttackKind,
    apply_training_data_attack,
    apply_update_attack,
)


def test_label_flip_and_backdoor_data_attacks_are_deterministic() -> None:
    examples = (
        ((-1.0, -1.0, 0.0), 0),
        ((1.0, 1.0, 0.0), 1),
        ((-0.5, -1.5, 0.0), 0),
    )
    flipped = apply_training_data_attack(examples, AttackKind.LABEL_FLIP)
    assert tuple(label for _, label in flipped) == (1, 0, 1)

    backdoored = apply_training_data_attack(examples, AttackKind.BACKDOOR)
    assert backdoored[0] == ((-1.0, -1.0, 1.0), 1)
    assert backdoored[1] == examples[1]
    assert backdoored[2] == examples[2]


def test_model_replacement_and_sign_flip_update_attacks() -> None:
    update = (1.0, -2.0, 0.5)
    assert apply_update_attack(
        update,
        AttackKind.MODEL_REPLACEMENT,
        scale=4.0,
    ) == pytest.approx((4.0, -8.0, 2.0))
    assert apply_update_attack(
        update,
        AttackKind.SIGN_FLIP,
        scale=4.0,
    ) == pytest.approx((-4.0, 8.0, -2.0))


@pytest.mark.parametrize(
    "strategy",
    ["median", "trimmed_mean", "krum", "multi_krum"],
)
def test_robust_strategies_resist_sign_flip_poisoning(strategy: str) -> None:
    result = run_robustness_trial(
        RobustnessTrialConfig(
            strategy=strategy,
            attack=AttackKind.SIGN_FLIP,
            seed=3,
        )
    )
    assert result.clean_accuracy > 0.95
    assert result.attack_success_rate < 0.05


def test_plain_mean_is_not_misrepresented_as_byzantine_robust() -> None:
    result = run_robustness_trial(
        RobustnessTrialConfig(
            strategy="mean",
            attack=AttackKind.SIGN_FLIP,
            seed=3,
        )
    )
    assert result.clean_accuracy < 0.20
    assert result.attack_success_rate > 0.80


def test_robust_aggregation_reduces_backdoor_attack_success() -> None:
    mean = run_robustness_benchmark(
        strategy="mean",
        attack=AttackKind.BACKDOOR,
        seeds=(1, 2, 3),
    )
    median = run_robustness_benchmark(
        strategy="median",
        attack=AttackKind.BACKDOOR,
        seeds=(1, 2, 3),
    )
    assert mean.attack_success_rate > 0.05
    assert median.attack_success_rate < 0.03
    assert median.attack_success_rate < mean.attack_success_rate / 2.0
    assert median.attacked_accuracy > 0.97


def test_multiseed_robustness_summary_is_reproducible() -> None:
    first = run_robustness_benchmark(
        strategy="trimmed_mean",
        attack=AttackKind.MODEL_REPLACEMENT,
        seeds=(1, 2, 3),
    )
    second = run_robustness_benchmark(
        strategy="trimmed_mean",
        attack=AttackKind.MODEL_REPLACEMENT,
        seeds=(1, 2, 3),
    )
    assert first == second
    assert first.baseline_accuracy > 0.97
    assert first.attacked_accuracy > 0.97
    assert first.accuracy_degradation < 0.02
