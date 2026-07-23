import copy
import unittest

import numpy as np
import torch

from data.partitioner import partition_dirichlet, partition_pathological
from federated.client import Client
from federated.dp_accountant import MomentsAccountant
from federated.server import Server
from models.networks import build_model
from utils.metrics import compute_client_drift, compute_weight_variance


class SyntheticDataset(torch.utils.data.Dataset):
    def __init__(self, samples: int = 40, classes: int = 4) -> None:
        self.data = torch.randn(samples, 1, 32, 32)
        self.targets = torch.tensor([index % classes for index in range(samples)])

    def __len__(self) -> int:
        return int(self.targets.numel())

    def __getitem__(self, index: int):
        return self.data[index], self.targets[index]


def base_config() -> dict:
    return {
        "system": {"seed": 7, "device": "cpu", "results_dir": "results"},
        "data": {
            "dataset": "MNIST",
            "data_root": "./data_raw",
            "partition": "dirichlet",
            "alpha": 0.3,
            "classes_per_client": 2,
            "min_partition_size": 2,
        },
        "federated": {
            "num_clients": 4,
            "sample_rate": 0.5,
            "rounds": 1,
            "local_epochs": 1,
            "batch_size": 4,
            "server_lr": 1.0,
        },
        "optimizer": {"lr": 0.01, "momentum": 0.0, "weight_decay": 0.0},
        "algorithm": {"name": "fedavg", "mu": 0.01},
        "dp": {
            "enabled": False,
            "max_grad_norm": 1.0,
            "noise_multiplier": 0.0,
            "target_delta": 1e-5,
        },
        "model": {"name": "cnn", "group_norm_groups": 2},
        "evaluation": {"eval_batch_size": 8},
    }


class LegacyBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        torch.manual_seed(123)
        np.random.seed(123)
        self.dataset = SyntheticDataset()

    def test_model_initialization_is_deterministic(self) -> None:
        torch.manual_seed(11)
        model_a = build_model("cnn", num_classes=4, in_channels=1, group_norm_groups=2)
        torch.manual_seed(11)
        model_b = build_model("cnn", num_classes=4, in_channels=1, group_norm_groups=2)
        for (name_a, tensor_a), (name_b, tensor_b) in zip(
            model_a.state_dict().items(), model_b.state_dict().items()
        ):
            self.assertEqual(name_a, name_b)
            self.assertTrue(torch.equal(tensor_a, tensor_b))

    def test_dirichlet_partitioning_is_seeded(self) -> None:
        part_a = partition_dirichlet(
            self.dataset, num_clients=4, alpha=0.3, seed=5, min_partition_size=2
        )
        part_b = partition_dirichlet(
            self.dataset, num_clients=4, alpha=0.3, seed=5, min_partition_size=2
        )
        for key in part_a:
            self.assertTrue(np.array_equal(part_a[key], part_b[key]))

    def test_pathological_partitioning_is_seeded(self) -> None:
        part_a = partition_pathological(
            self.dataset, num_clients=4, classes_per_client=1, seed=9
        )
        part_b = partition_pathological(
            self.dataset, num_clients=4, classes_per_client=1, seed=9
        )
        for key in part_a:
            self.assertTrue(np.array_equal(part_a[key], part_b[key]))

    def test_rdp_accountant_progresses(self) -> None:
        accountant = MomentsAccountant(
            noise_multiplier=0.8, sample_rate=0.25, target_delta=1e-5
        )
        self.assertEqual(accountant.get_epsilon(), 0.0)
        accountant.step()
        self.assertGreater(accountant.get_epsilon(), 0.0)

    def test_weight_variance_and_client_drift_are_deterministic(self) -> None:
        state_a = {"w": torch.tensor([1.0, 2.0])}
        state_b = {"w": torch.tensor([3.0, 4.0])}
        delta_a = {"w": torch.tensor([1.0, 0.0])}
        delta_b = {"w": torch.tensor([0.0, 1.0])}
        self.assertAlmostEqual(compute_weight_variance([state_a, state_b]), 1.0)
        self.assertAlmostEqual(
            compute_client_drift([delta_a, delta_b]), np.sqrt(0.5), places=6
        )

    def test_server_fedavg_weighting(self) -> None:
        model = build_model("cnn", num_classes=4, in_channels=1, group_norm_groups=2)
        server = Server(
            model=model,
            num_clients=4,
            algorithm="fedavg",
            server_lr=1.0,
            device=torch.device("cpu"),
        )
        before = copy.deepcopy(server.broadcast())
        float_key = next(
            name for name, tensor in before.items() if torch.is_floating_point(tensor)
        )
        delta_one = {float_key: torch.ones_like(before[float_key])}
        delta_two = {float_key: torch.zeros_like(before[float_key])}
        server._aggregate_weighted(
            [
                {"num_samples": 3, "delta": delta_one},
                {"num_samples": 1, "delta": delta_two},
            ]
        )
        after = server.broadcast()
        expected = before[float_key] + 0.75 * torch.ones_like(before[float_key])
        self.assertTrue(torch.allclose(after[float_key], expected))

    def test_client_dp_disabled_preserves_local_delta(self) -> None:
        config = base_config()
        indices = np.arange(8, dtype=np.int64)
        client = Client(0, self.dataset, indices, config, torch.device("cpu"))
        model = build_model("cnn", num_classes=4, in_channels=1, group_norm_groups=2)
        global_state = {
            k: v.detach().cpu().clone() for k, v in model.state_dict().items()
        }
        result = client.train(
            model=model, global_state=global_state, algorithm="fedavg"
        )
        recomputed = {
            name: result["local_state"][name] - global_state[name]
            for name, value in result["local_state"].items()
            if torch.is_floating_point(value)
        }
        for key in recomputed:
            self.assertTrue(torch.allclose(result["delta"][key], recomputed[key]))

    def test_client_dp_clipping_reduces_update_norm(self) -> None:
        config = base_config()
        config["dp"]["enabled"] = True
        config["dp"]["noise_multiplier"] = 0.0
        config["dp"]["max_grad_norm"] = 0.05
        indices = np.arange(8, dtype=np.int64)
        client = Client(0, self.dataset, indices, config, torch.device("cpu"))
        model = build_model("cnn", num_classes=4, in_channels=1, group_norm_groups=2)
        global_state = {
            k: v.detach().cpu().clone() for k, v in model.state_dict().items()
        }
        result = client.train(
            model=model, global_state=global_state, algorithm="fedavg"
        )
        norm_sq = 0.0
        for tensor in result["delta"].values():
            norm_sq += float(tensor.pow(2).sum().item())
        self.assertLessEqual(norm_sq**0.5, 0.050001)


if __name__ == "__main__":
    unittest.main()
