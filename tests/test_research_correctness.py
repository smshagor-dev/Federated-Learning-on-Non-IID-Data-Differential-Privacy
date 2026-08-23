from __future__ import annotations

import unittest

import torch
import torch.nn as nn

from federated.privacy_research import (
    CLIENT_ADD_REMOVE_ADJACENCY,
    RDPMechanism,
    calibrate_noise_multiplier,
    compose_same_adjacency_rdp,
    epsilon_for_client_level_gaussian,
)
from federated.server import Server


class PrivacyCalibrationTests(unittest.TestCase):
    def test_calibration_returns_privacy_safe_sigma(self) -> None:
        result = calibrate_noise_multiplier(
            target_epsilon=4.0,
            sample_rate=0.2,
            steps=50,
            delta=1e-5,
            epsilon_tolerance=1e-5,
        )
        self.assertGreater(result.noise_multiplier, 0.0)
        self.assertLessEqual(result.achieved_epsilon, result.target_epsilon + 1e-10)
        self.assertLess(result.target_epsilon - result.achieved_epsilon, 1e-3)

    def test_stricter_budget_requires_at_least_as_much_noise(self) -> None:
        strict = calibrate_noise_multiplier(
            target_epsilon=2.0,
            sample_rate=0.2,
            steps=50,
            delta=1e-5,
        )
        relaxed = calibrate_noise_multiplier(
            target_epsilon=8.0,
            sample_rate=0.2,
            steps=50,
            delta=1e-5,
        )
        self.assertGreater(strict.noise_multiplier, relaxed.noise_multiplier)

    def test_calibrated_sigma_reproduces_reported_epsilon(self) -> None:
        result = calibrate_noise_multiplier(
            target_epsilon=6.0,
            sample_rate=0.1,
            steps=100,
            delta=1e-5,
        )
        epsilon = epsilon_for_client_level_gaussian(
            noise_multiplier=result.noise_multiplier,
            sample_rate=result.sample_rate,
            steps=result.steps,
            delta=result.delta,
        )
        self.assertAlmostEqual(epsilon, result.achieved_epsilon, places=12)


class PrivacyCompositionTests(unittest.TestCase):
    def test_same_adjacency_rdp_composes(self) -> None:
        first = RDPMechanism(
            name="model-release",
            adjacency=CLIENT_ADD_REMOVE_ADJACENCY,
            sample_rate=0.1,
            noise_multiplier=1.2,
            steps=50,
        )
        second = RDPMechanism(
            name="private-statistic",
            adjacency=CLIENT_ADD_REMOVE_ADJACENCY,
            sample_rate=0.1,
            noise_multiplier=2.0,
            steps=50,
        )
        composed = compose_same_adjacency_rdp(
            mechanisms=[first, second],
            delta=1e-5,
        )
        first_only = compose_same_adjacency_rdp(
            mechanisms=[first],
            delta=1e-5,
        )
        second_only = compose_same_adjacency_rdp(
            mechanisms=[second],
            delta=1e-5,
        )
        self.assertGreaterEqual(composed.epsilon, first_only.epsilon)
        self.assertGreaterEqual(composed.epsilon, second_only.epsilon)
        self.assertEqual(composed.adjacency, CLIENT_ADD_REMOVE_ADJACENCY)

    def test_mixed_adjacency_is_rejected(self) -> None:
        client_level = RDPMechanism(
            name="client-level",
            adjacency=CLIENT_ADD_REMOVE_ADJACENCY,
            sample_rate=0.1,
            noise_multiplier=1.0,
            steps=10,
        )
        sample_level = RDPMechanism(
            name="sample-level",
            adjacency="sample_add_remove_within_client",
            sample_rate=0.05,
            noise_multiplier=1.0,
            steps=10,
        )
        with self.assertRaisesRegex(ValueError, "different neighboring relations"):
            compose_same_adjacency_rdp(
                mechanisms=[client_level, sample_level],
                delta=1e-5,
            )


class ScaffoldPrivacyBoundaryTests(unittest.TestCase):
    def test_dp_scaffold_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "DP-enabled SCAFFOLD is disabled"):
            Server(
                model=nn.Linear(2, 2),
                num_clients=2,
                algorithm="scaffold",
                device=torch.device("cpu"),
                dp_enabled=True,
                noise_multiplier=1.0,
                update_clip_norm=1.0,
            )

    def test_non_private_scaffold_remains_available(self) -> None:
        server = Server(
            model=nn.Linear(2, 2),
            num_clients=2,
            algorithm="scaffold",
            device=torch.device("cpu"),
            dp_enabled=False,
        )
        self.assertEqual(server.algorithm, "scaffold")


if __name__ == "__main__":
    unittest.main()
