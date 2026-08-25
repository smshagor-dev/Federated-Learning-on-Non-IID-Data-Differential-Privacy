"""Model-level adversarial benchmark for v3 robust aggregation.

The benchmark trains a small binary logistic model over deterministic synthetic
federated clients. It exercises the same ``V3AggregationEngine`` used by the
execution bridge, so robustness evidence covers runtime-integrated aggregation
rather than only standalone vector helpers.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass

from fl_platform.v3.attacks import (
    AttackKind,
    LabeledExample,
    apply_training_data_attack,
    apply_update_attack,
)
from fl_platform.v3.runtime_integration import (
    AggregationConfig,
    V3AggregationEngine,
)
from fl_platform.workers import TrainingResult

Model = tuple[float, float, float, float]


@dataclass(frozen=True)
class RobustnessTrialConfig:
    strategy: str
    attack: AttackKind
    seed: int
    clients: int = 10
    malicious_clients: int = 2
    samples_per_client: int = 40
    test_samples: int = 400
    rounds: int = 10
    learning_rate: float = 0.08
    local_epochs: int = 1
    attack_scale: float = 12.0

    def validate(self) -> None:
        if self.clients < 3:
            raise ValueError("clients must be at least 3")
        if not 0 <= self.malicious_clients < self.clients:
            raise ValueError("malicious_clients must be in [0, clients)")
        if self.samples_per_client <= 0 or self.test_samples <= 0:
            raise ValueError("sample counts must be positive")
        if self.rounds <= 0 or self.local_epochs <= 0:
            raise ValueError("rounds and local_epochs must be positive")
        if self.learning_rate <= 0.0 or not math.isfinite(self.learning_rate):
            raise ValueError("learning_rate must be finite and positive")
        if self.attack_scale <= 0.0 or not math.isfinite(self.attack_scale):
            raise ValueError("attack_scale must be finite and positive")
        if self.strategy in {"krum", "multi_krum"}:
            minimum = 2 * self.malicious_clients + 3
            if self.clients < minimum:
                raise ValueError("Krum strategies require clients >= 2f + 3")


@dataclass(frozen=True)
class RobustnessTrialResult:
    strategy: str
    attack: str
    clean_accuracy: float
    attack_success_rate: float
    model: Model


@dataclass(frozen=True)
class RobustnessBenchmarkSummary:
    strategy: str
    attack: str
    seeds: tuple[int, ...]
    baseline_accuracy: float
    attacked_accuracy: float
    accuracy_degradation: float
    attack_success_rate: float
    attacked_accuracy_stddev: float


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        exp_value = math.exp(-value)
        return 1.0 / (1.0 + exp_value)
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _make_dataset(seed: int, size: int) -> tuple[LabeledExample, ...]:
    rng = random.Random(seed)
    examples: list[LabeledExample] = []
    for index in range(size):
        label = index % 2
        center_x = -1.5 if label == 0 else 1.5
        center_y = -1.0 if label == 0 else 1.0
        features = (
            rng.gauss(center_x, 0.8),
            rng.gauss(center_y, 0.8),
            0.0,
        )
        examples.append((features, label))
    rng.shuffle(examples)
    return tuple(examples)


def _local_update(
    model: Model,
    examples: tuple[LabeledExample, ...],
    *,
    learning_rate: float,
    local_epochs: int,
) -> Model:
    weights = list(model)
    for _ in range(local_epochs):
        for features, label in examples:
            logit = (
                weights[0] * features[0]
                + weights[1] * features[1]
                + weights[2] * features[2]
                + weights[3]
            )
            error = _sigmoid(logit) - label
            weights[0] -= learning_rate * error * features[0]
            weights[1] -= learning_rate * error * features[1]
            weights[2] -= learning_rate * error * features[2]
            weights[3] -= learning_rate * error
    return tuple(weights[index] - model[index] for index in range(4))  # type: ignore[return-value]


def _predict(model: Model, features: tuple[float, ...]) -> int:
    logit = (
        model[0] * features[0]
        + model[1] * features[1]
        + model[2] * features[2]
        + model[3]
    )
    return int(_sigmoid(logit) >= 0.5)


def _accuracy(model: Model, examples: tuple[LabeledExample, ...]) -> float:
    correct = sum(_predict(model, features) == label for features, label in examples)
    return correct / len(examples)


def _backdoor_success_rate(
    model: Model,
    examples: tuple[LabeledExample, ...],
    *,
    target_label: int = 1,
) -> float:
    source_label = 1 - target_label
    source = [features for features, label in examples if label == source_label]
    if not source:
        return 0.0
    successes = 0
    for features in source:
        triggered = (features[0], features[1], 1.0)
        successes += _predict(model, triggered) == target_label
    return successes / len(source)


def _aggregation_config(config: RobustnessTrialConfig) -> AggregationConfig:
    robust = {"median", "trimmed_mean", "krum", "multi_krum"}
    if config.strategy not in {"mean", *robust}:
        raise ValueError(f"unsupported benchmark strategy: {config.strategy}")
    trim_ratio = 0.0
    if config.strategy == "trimmed_mean":
        trim_ratio = config.malicious_clients / config.clients
    select = max(1, config.clients - config.malicious_clients - 2)
    return AggregationConfig(
        strategy=config.strategy,
        weighting="uniform",
        trim_ratio=trim_ratio,
        byzantine_clients=config.malicious_clients,
        multi_krum_select=select,
    )


def run_robustness_trial(config: RobustnessTrialConfig) -> RobustnessTrialResult:
    """Train one deterministic federated robustness scenario."""
    config.validate()
    model: Model = (0.0, 0.0, 0.0, 0.0)
    client_data = tuple(
        _make_dataset(config.seed * 100 + client_id, config.samples_per_client)
        for client_id in range(config.clients)
    )
    engine = V3AggregationEngine(4, _aggregation_config(config))

    for round_id in range(config.rounds):
        results: list[TrainingResult] = []
        for client_id, examples in enumerate(client_data):
            malicious = client_id < config.malicious_clients
            training_data = examples
            if malicious:
                training_data = apply_training_data_attack(
                    examples,
                    config.attack,
                )
            update = _local_update(
                model,
                training_data,
                learning_rate=config.learning_rate,
                local_epochs=config.local_epochs,
            )
            if malicious:
                update = apply_update_attack(
                    update,
                    config.attack,
                    scale=config.attack_scale,
                )  # type: ignore[assignment]
            results.append(
                TrainingResult(
                    run_id="v3-robustness",
                    round_id=round_id,
                    client_id=f"client-{client_id}",
                    model_version=f"model-{round_id}",
                    sample_count=len(training_data),
                    local_step_count=config.local_epochs * len(training_data),
                    model_update=update,
                )
            )
        aggregate = engine.aggregate(results).update
        model = tuple(
            model[index] + aggregate[index] for index in range(4)
        )  # type: ignore[assignment]

    test_data = _make_dataset(config.seed + 999, config.test_samples)
    clean_accuracy = _accuracy(model, test_data)
    if config.attack == AttackKind.BACKDOOR:
        attack_success = _backdoor_success_rate(model, test_data)
    elif config.attack == AttackKind.NONE:
        attack_success = 0.0
    else:
        attack_success = 1.0 - clean_accuracy
    return RobustnessTrialResult(
        strategy=config.strategy,
        attack=config.attack.value,
        clean_accuracy=clean_accuracy,
        attack_success_rate=attack_success,
        model=model,
    )


def run_robustness_benchmark(
    *,
    strategy: str,
    attack: AttackKind,
    seeds: tuple[int, ...] = (1, 2, 3, 4, 5),
    malicious_clients: int = 2,
) -> RobustnessBenchmarkSummary:
    """Compare attacked trials against same-strategy clean baselines."""
    if not seeds:
        raise ValueError("seeds must not be empty")
    baselines: list[float] = []
    attacked_accuracy: list[float] = []
    attack_success: list[float] = []
    for seed in seeds:
        baseline = run_robustness_trial(
            RobustnessTrialConfig(
                strategy=strategy,
                attack=AttackKind.NONE,
                seed=seed,
                malicious_clients=malicious_clients,
            )
        )
        attacked = run_robustness_trial(
            RobustnessTrialConfig(
                strategy=strategy,
                attack=attack,
                seed=seed,
                malicious_clients=malicious_clients,
            )
        )
        baselines.append(baseline.clean_accuracy)
        attacked_accuracy.append(attacked.clean_accuracy)
        attack_success.append(attacked.attack_success_rate)

    baseline_mean = statistics.fmean(baselines)
    attacked_mean = statistics.fmean(attacked_accuracy)
    return RobustnessBenchmarkSummary(
        strategy=strategy,
        attack=attack.value,
        seeds=seeds,
        baseline_accuracy=baseline_mean,
        attacked_accuracy=attacked_mean,
        accuracy_degradation=baseline_mean - attacked_mean,
        attack_success_rate=statistics.fmean(attack_success),
        attacked_accuracy_stddev=statistics.pstdev(attacked_accuracy),
    )


__all__ = [
    "RobustnessBenchmarkSummary",
    "RobustnessTrialConfig",
    "RobustnessTrialResult",
    "run_robustness_benchmark",
    "run_robustness_trial",
]
