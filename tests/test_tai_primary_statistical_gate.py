"""A one-round pilot must NEVER be counted as a 50-round research cell."""

from __future__ import annotations

import copy

import pytest

from tai_pfl.analysis import assert_primary_50round_protocol


@pytest.fixture
def valid_primary() -> dict:
    return {
        "request": {
            "dataset": "MNIST",
            "partition": "iid",
            "method": "fedavg",
            "seed": 11,
            "rounds": 50,
            "training": {
                "batch_size": 64,
                "local_epochs": 2,
                "participation": 0.2,
                "adaptation_steps": 1,
            },
        },
        "metrics": {"client_count": 20},
    }


def test_primary_gate_accepts_actual_50_round_protocol(valid_primary: dict) -> None:
    assert assert_primary_50round_protocol(valid_primary) is None


@pytest.mark.parametrize(
    ("path", "incorrect"),
    [
        (("request", "rounds"), 1),
        (("request", "training", "local_epochs"), 1),
        (("request", "training", "batch_size"), 128),
        (("request", "training", "participation"), 0.05),
        (("request", "training", "adaptation_steps"), 5),
        (("metrics", "client_count"), 10),
        (("request", "dataset"), "SYNTHETIC"),
        (("request", "partition"), "dirichlet:2.0"),
        (("request", "method"), "not-a-method"),
        (("request", "seed"), 99999),
    ],
)
def test_primary_gate_rejects_pilot_and_nonprimary_protocol(
    valid_primary: dict, path: tuple[str, ...], incorrect: object
) -> None:
    case = copy.deepcopy(valid_primary)
    value = case
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = incorrect
    with pytest.raises(ValueError):
        assert_primary_50round_protocol(case)
