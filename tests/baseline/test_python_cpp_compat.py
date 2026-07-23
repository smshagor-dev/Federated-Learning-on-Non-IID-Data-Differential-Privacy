"""Python-to-C++ aggregation golden compatibility tests (Milestone 2, Work
Package D).

These tests build deterministic PyTorch state dicts and client updates,
send them through the compiled C++ aggregation core via
``fl_platform.compat.cpp_bridge``, and compare the result against:

* the legacy Python ``federated.server.Server`` implementation, for
  FedAvg / FedProx / SCAFFOLD, since that is real, independently
  maintained server-side aggregation code (not re-derived math), or
* a small independently-written reference of the published FedOpt
  update rule, for FedAdagrad / FedAdam / FedYogi, since the legacy
  prototype has no server-side optimizer implementation to compare
  against.

All tests skip (rather than fail) if the C++ CLI has not been built, so
this file is safe to collect on machines without a C++ toolchain.
"""

from __future__ import annotations

import copy
import math
import unittest

import torch
import torch.nn as nn

from federated.server import Server
from fl_platform.compat.cpp_bridge import (
    CppAggregationError,
    CppAggregationRequest,
    CppAggregationUnavailable,
    CppClientUpdate,
    apply_delta_to_state_dict,
    find_cli,
    run_cpp_aggregate,
)


def _require_cli() -> None:
    try:
        find_cli()
    except CppAggregationUnavailable as error:
        raise unittest.SkipTest(str(error)) from error


def _toy_model() -> nn.Linear:
    torch.manual_seed(0)
    model = nn.Linear(2, 2, bias=True)
    with torch.no_grad():
        model.weight.copy_(torch.tensor([[0.1, 0.2], [0.3, 0.4]]))
        model.bias.copy_(torch.tensor([0.5, -0.5]))
    return model


def _assert_state_close(test: unittest.TestCase, actual, expected, msg: str) -> None:
    for name, tensor in expected.items():
        test.assertTrue(
            torch.allclose(actual[name], tensor, atol=1e-6),
            f"{msg}: mismatch on '{name}': {actual[name]} vs {tensor}",
        )


class FedAvgFedProxParityTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_cli()

    def test_fedavg_equal_sample_counts(self) -> None:
        model = _toy_model()
        legacy_model = copy.deepcopy(model)
        server = Server(legacy_model, num_clients=2, algorithm="fedavg")

        delta_a = {"weight": torch.tensor([[1.0, 2.0], [3.0, 4.0]]), "bias": torch.tensor([1.0, -1.0])}
        delta_b = {"weight": torch.zeros(2, 2), "bias": torch.zeros(2)}

        server.aggregate([
            {"num_samples": 4, "delta": delta_a},
            {"num_samples": 4, "delta": delta_b},
        ])
        expected_state = server.model.state_dict()

        request = CppAggregationRequest(
            algorithm="fedavg",
            weighting="sample_count",
            updates=[
                CppClientUpdate("c1", "u1", "n1", "w1", "v1", 4, delta_a),
                CppClientUpdate("c2", "u2", "n2", "w2", "v1", 4, delta_b),
            ],
            model_version="v1",
        )
        result = run_cpp_aggregate(request)
        reconstructed = apply_delta_to_state_dict(model.state_dict(), result.model_delta)
        _assert_state_close(self, reconstructed, expected_state, "fedavg equal sample counts")

    def test_fedavg_unequal_sample_counts(self) -> None:
        model = _toy_model()
        legacy_model = copy.deepcopy(model)
        server = Server(legacy_model, num_clients=2, algorithm="fedavg")

        delta_a = {"weight": torch.tensor([[2.0, 0.0], [0.0, 2.0]]), "bias": torch.tensor([0.4, 0.2])}
        delta_b = {"weight": torch.tensor([[0.0, 4.0], [4.0, 0.0]]), "bias": torch.tensor([-0.2, 0.6])}

        server.aggregate([
            {"num_samples": 9, "delta": delta_a},
            {"num_samples": 1, "delta": delta_b},
        ])
        expected_state = server.model.state_dict()

        request = CppAggregationRequest(
            algorithm="fedavg",
            weighting="sample_count",
            updates=[
                CppClientUpdate("c1", "u1", "n1", "w1", "v1", 9, delta_a),
                CppClientUpdate("c2", "u2", "n2", "w2", "v1", 1, delta_b),
            ],
            model_version="v1",
        )
        result = run_cpp_aggregate(request)
        reconstructed = apply_delta_to_state_dict(model.state_dict(), result.model_delta)
        _assert_state_close(self, reconstructed, expected_state, "fedavg unequal sample counts")

    def test_uniform_weighting(self) -> None:
        # Server has no uniform-weighting mode; compute the expected delta
        # by hand (unweighted mean) instead.
        delta_a = {"weight": torch.tensor([[1.0, 1.0], [1.0, 1.0]]), "bias": torch.tensor([1.0, 1.0])}
        delta_b = {"weight": torch.tensor([[3.0, 3.0], [3.0, 3.0]]), "bias": torch.tensor([3.0, 3.0])}
        expected_delta = {
            "weight": (delta_a["weight"] + delta_b["weight"]) / 2.0,
            "bias": (delta_a["bias"] + delta_b["bias"]) / 2.0,
        }

        request = CppAggregationRequest(
            algorithm="fedavg",
            weighting="uniform",
            updates=[
                CppClientUpdate("c1", "u1", "n1", "w1", "v1", 100, delta_a),
                CppClientUpdate("c2", "u2", "n2", "w2", "v1", 1, delta_b),
            ],
            model_version="v1",
        )
        result = run_cpp_aggregate(request)
        _assert_state_close(self, result.model_delta, expected_delta, "uniform weighting")

    def test_capped_sample_count_weighting(self) -> None:
        delta_a = {"weight": torch.tensor([[1.0, 0.0], [0.0, 1.0]]), "bias": torch.tensor([1.0, 0.0])}
        delta_b = {"weight": torch.tensor([[0.0, 1.0], [1.0, 0.0]]), "bias": torch.tensor([0.0, 1.0])}
        # bounded_n = min(n, cap): client A capped at 2, client B stays at 1.
        weight_a, weight_b = 2.0 / 3.0, 1.0 / 3.0
        expected_delta = {
            "weight": weight_a * delta_a["weight"] + weight_b * delta_b["weight"],
            "bias": weight_a * delta_a["bias"] + weight_b * delta_b["bias"],
        }

        request = CppAggregationRequest(
            algorithm="fedavg",
            weighting="capped_sample_count",
            contribution_cap=2.0,
            updates=[
                CppClientUpdate("c1", "u1", "n1", "w1", "v1", 10, delta_a),
                CppClientUpdate("c2", "u2", "n2", "w2", "v1", 1, delta_b),
            ],
            model_version="v1",
        )
        result = run_cpp_aggregate(request)
        _assert_state_close(self, result.model_delta, expected_delta, "capped sample count weighting")

    def test_fedprox_server_behavior_matches_fedavg(self) -> None:
        model = _toy_model()
        legacy_model = copy.deepcopy(model)
        server = Server(legacy_model, num_clients=2, algorithm="fedprox")

        delta_a = {"weight": torch.tensor([[1.0, -1.0], [-1.0, 1.0]]), "bias": torch.tensor([0.1, -0.1])}
        delta_b = {"weight": torch.zeros(2, 2), "bias": torch.zeros(2)}

        server.aggregate([
            {"num_samples": 3, "delta": delta_a},
            {"num_samples": 1, "delta": delta_b},
        ])
        expected_state = server.model.state_dict()

        request = CppAggregationRequest(
            algorithm="fedprox",
            weighting="sample_count",
            updates=[
                CppClientUpdate("c1", "u1", "n1", "w1", "v1", 3, delta_a),
                CppClientUpdate("c2", "u2", "n2", "w2", "v1", 1, delta_b),
            ],
            model_version="v1",
        )
        result = run_cpp_aggregate(request)
        reconstructed = apply_delta_to_state_dict(model.state_dict(), result.model_delta)
        _assert_state_close(self, reconstructed, expected_state, "fedprox server behavior")


class ScaffoldParityTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_cli()

    def test_scaffold_model_and_control_variate_update(self) -> None:
        model = _toy_model()
        legacy_model = copy.deepcopy(model)
        num_clients = 5
        server = Server(legacy_model, num_clients=num_clients, algorithm="scaffold")
        original_c_global = {k: v.clone() for k, v in server.c_global.items()}

        delta_a = {"weight": torch.tensor([[1.0, 2.0], [3.0, 4.0]]), "bias": torch.tensor([1.0, 1.0])}
        delta_b = {"weight": torch.tensor([[5.0, 6.0], [7.0, 8.0]]), "bias": torch.tensor([-1.0, -1.0])}
        dc_a = {"weight": torch.full((2, 2), 0.4), "bias": torch.full((2,), 0.4)}
        dc_b = {"weight": torch.full((2, 2), 0.8), "bias": torch.full((2,), 0.8)}

        server.aggregate([
            {
                "num_samples": 4,
                "delta": delta_a,
                "delta_c": dc_a,
                "client_id": 0,
                "new_c_local": {k: v.clone() for k, v in dc_a.items()},
            },
            {
                "num_samples": 4,
                "delta": delta_b,
                "delta_c": dc_b,
                "client_id": 1,
                "new_c_local": {k: v.clone() for k, v in dc_b.items()},
            },
        ])
        expected_state = server.model.state_dict()
        expected_c_global = server.c_global

        request = CppAggregationRequest(
            algorithm="scaffold",
            total_clients=num_clients,
            updates=[
                CppClientUpdate("c1", "u1", "n1", "w1", "v1", 4, delta_a, dc_a),
                CppClientUpdate("c2", "u2", "n2", "w2", "v1", 4, delta_b, dc_b),
            ],
            model_version="v1",
        )
        result = run_cpp_aggregate(request)

        reconstructed_model = apply_delta_to_state_dict(model.state_dict(), result.model_delta)
        _assert_state_close(self, reconstructed_model, expected_state, "scaffold model update")

        reconstructed_c_global = apply_delta_to_state_dict(original_c_global, result.control_delta)
        _assert_state_close(self, reconstructed_c_global, expected_c_global, "scaffold control variate update")


def _fedopt_reference_step(algorithm, delta, first_moment, second_moment, step, beta1, beta2, tau, server_lr):
    """Independently-written reference of the FedOpt server update rule
    (Reddi et al., 2020), used to check the C++ implementation and the
    Python<->C++ tensor round trip, not to duplicate C++ source code."""
    next_step = step + 1
    new_first = {name: beta1 * first_moment[name] + (1 - beta1) * delta[name] for name in delta}

    if algorithm == "fedadagrad":
        new_second = {name: second_moment[name] + delta[name] ** 2 for name in delta}
        m_hat = new_first
        v_hat = new_second
    elif algorithm == "fedadam":
        new_second = {
            name: beta2 * second_moment[name] + (1 - beta2) * (delta[name] ** 2) for name in delta
        }
        m_hat = {name: new_first[name] / (1 - beta1**next_step) for name in delta}
        v_hat = {name: new_second[name] / (1 - beta2**next_step) for name in delta}
    elif algorithm == "fedyogi":
        grad_sq = {name: delta[name] ** 2 for name in delta}
        new_second = {
            name: second_moment[name]
            - (1 - beta2) * torch.sign(second_moment[name] - grad_sq[name]) * grad_sq[name]
            for name in delta
        }
        m_hat = {name: new_first[name] / (1 - beta1**next_step) for name in delta}
        v_hat = {name: new_second[name] / (1 - beta2**next_step) for name in delta}
    else:
        raise ValueError(algorithm)

    model_delta = {name: server_lr * m_hat[name] / (torch.sqrt(v_hat[name]) + tau) for name in delta}
    return model_delta, new_first, new_second, next_step


class FedOptParityTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_cli()

    def _run_two_rounds(self, algorithm: str) -> None:
        beta1 = 0.0 if algorithm == "fedadagrad" else 0.9
        beta2, tau, server_lr = 0.99, 1.0, 1.0

        round_one_delta = {"weight": torch.tensor([[1.0, -1.0], [0.5, 0.0]]), "bias": torch.tensor([0.2, -0.2])}
        round_two_delta = {"weight": torch.tensor([[0.5, 0.5], [-0.5, 1.0]]), "bias": torch.tensor([0.1, 0.1])}

        zero_state = {"weight": torch.zeros(2, 2), "bias": torch.zeros(2)}
        ref_delta_1, ref_first_1, ref_second_1, _ = _fedopt_reference_step(
            algorithm, round_one_delta, zero_state, zero_state, 0, beta1, beta2, tau, server_lr
        )
        ref_delta_2, _, _, _ = _fedopt_reference_step(
            algorithm, round_two_delta, ref_first_1, ref_second_1, 1, beta1, beta2, tau, server_lr
        )

        base_request_kwargs = dict(
            algorithm=algorithm,
            weighting="uniform",
            beta1=beta1,
            beta2=beta2,
            tau=tau,
            server_lr=server_lr,
            model_version="v1",
        )

        round_one_request = CppAggregationRequest(
            updates=[CppClientUpdate("c1", "u1", "n1", "w1", "v1", 1, round_one_delta)],
            **base_request_kwargs,
        )
        round_one_result = run_cpp_aggregate(round_one_request)
        _assert_state_close(self, round_one_result.model_delta, ref_delta_1, f"{algorithm} round 1")
        self.assertEqual(round_one_result.optimizer_step, 1)

        round_two_request = CppAggregationRequest(
            updates=[CppClientUpdate("c1", "u2", "n2", "w1", "v1", 1, round_two_delta)],
            previous_step=round_one_result.optimizer_step,
            previous_first_moment=round_one_result.first_moment,
            previous_second_moment=round_one_result.second_moment,
            **base_request_kwargs,
        )
        round_two_result = run_cpp_aggregate(round_two_request)
        _assert_state_close(self, round_two_result.model_delta, ref_delta_2, f"{algorithm} round 2")
        self.assertEqual(round_two_result.optimizer_step, 2)

    def test_fedadagrad_first_and_later_steps(self) -> None:
        self._run_two_rounds("fedadagrad")

    def test_fedadam_first_and_later_steps(self) -> None:
        self._run_two_rounds("fedadam")

    def test_fedyogi_first_and_later_steps(self) -> None:
        self._run_two_rounds("fedyogi")


class RejectionParityTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_cli()

    def test_invalid_tensor_rejected(self) -> None:
        delta = {"weight": torch.tensor([[math.nan, 0.0], [0.0, 0.0]]), "bias": torch.zeros(2)}
        request = CppAggregationRequest(
            algorithm="fedavg",
            updates=[CppClientUpdate("c1", "u1", "n1", "w1", "v1", 1, delta)],
            model_version="v1",
        )
        with self.assertRaises(CppAggregationError):
            run_cpp_aggregate(request)

    def test_stale_model_version_rejected(self) -> None:
        delta = {"weight": torch.zeros(2, 2), "bias": torch.zeros(2)}
        request = CppAggregationRequest(
            algorithm="fedavg",
            updates=[CppClientUpdate("c1", "u1", "n1", "w1", "v0-stale", 1, delta)],
            model_version="v1",
        )
        with self.assertRaises(CppAggregationError):
            run_cpp_aggregate(request)

    def test_duplicate_client_rejected(self) -> None:
        delta = {"weight": torch.zeros(2, 2), "bias": torch.zeros(2)}
        request = CppAggregationRequest(
            algorithm="fedavg",
            updates=[
                CppClientUpdate("dup", "u1", "n1", "w1", "v1", 1, delta),
                CppClientUpdate("dup", "u2", "n2", "w2", "v1", 1, delta),
            ],
            model_version="v1",
        )
        with self.assertRaises(CppAggregationError):
            run_cpp_aggregate(request)


if __name__ == "__main__":
    unittest.main()
