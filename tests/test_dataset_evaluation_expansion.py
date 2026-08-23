from __future__ import annotations

import os
import tempfile
import unittest

import numpy as np
import torch
from torch.utils.data import TensorDataset

from data.partitioner import (
    SUPPORTED_DATASETS,
    client_label_histograms,
    partition_evaluation_by_train_distribution,
)
from federated.server import Server
from utils.metrics import evaluate_client_partitions
from utils.runtime_args import parse_args


class TargetDataset:
    def __init__(self, targets: list[int]) -> None:
        self.targets = torch.tensor(targets, dtype=torch.long)

    def __len__(self) -> int:
        return int(self.targets.numel())


class DatasetExpansionTests(unittest.TestCase):
    def test_root_dataset_catalog_contains_four_real_datasets(self) -> None:
        self.assertEqual(
            SUPPORTED_DATASETS,
            ("MNIST", "FASHIONMNIST", "CIFAR10", "CIFAR100"),
        )

    def test_cli_accepts_expanded_dataset_names(self) -> None:
        cifar = parse_args(["--cli", "--dataset", "CIFAR100"])
        fashion = parse_args(["--cli", "--dataset", "FASHIONMNIST"])
        self.assertEqual(cifar.dataset, "CIFAR100")
        self.assertEqual(fashion.dataset, "FASHIONMNIST")


class MatchedEvaluationPartitionTests(unittest.TestCase):
    def test_heldout_partition_matches_training_label_proportions(self) -> None:
        train = TargetDataset([0] * 6 + [1] * 6)
        heldout = TargetDataset([0] * 6 + [1] * 6)
        train_partition = {
            0: np.asarray([0, 1, 2, 3, 6, 7], dtype=np.int64),
            1: np.asarray([4, 5, 8, 9, 10, 11], dtype=np.int64),
        }
        heldout_partition = partition_evaluation_by_train_distribution(
            train,
            train_partition,
            heldout,
            seed=17,
        )
        histograms = client_label_histograms(heldout_partition, heldout)
        self.assertEqual(histograms["client-0"], {0: 4, 1: 2})
        self.assertEqual(histograms["client-1"], {0: 2, 1: 4})
        assigned = np.concatenate(list(heldout_partition.values()))
        self.assertEqual(len(assigned), len(heldout))
        self.assertEqual(len(np.unique(assigned)), len(heldout))

    def test_matched_partition_is_deterministic(self) -> None:
        train = TargetDataset([0, 0, 0, 1, 1, 1, 1, 1])
        heldout = TargetDataset([0, 0, 0, 0, 1, 1, 1, 1])
        train_partition = {
            0: np.asarray([0, 1, 3, 4], dtype=np.int64),
            1: np.asarray([2, 5, 6, 7], dtype=np.int64),
        }
        first = partition_evaluation_by_train_distribution(
            train, train_partition, heldout, seed=91
        )
        second = partition_evaluation_by_train_distribution(
            train, train_partition, heldout, seed=91
        )
        self.assertEqual(first.keys(), second.keys())
        for client_id in first:
            np.testing.assert_array_equal(first[client_id], second[client_id])

    def test_empty_clients_receive_unique_heldout_samples(self) -> None:
        train = TargetDataset([0] * 100)
        heldout = TargetDataset([0, 0, 0])
        train_partition = {
            0: np.arange(0, 98, dtype=np.int64),
            1: np.asarray([98], dtype=np.int64),
            2: np.asarray([99], dtype=np.int64),
        }
        result = partition_evaluation_by_train_distribution(
            train, train_partition, heldout, seed=5
        )
        self.assertEqual([len(result[index]) for index in range(3)], [1, 1, 1])
        assigned = np.concatenate([result[index] for index in range(3)])
        self.assertEqual(set(assigned.tolist()), {0, 1, 2})


class HeldoutClientMetricTests(unittest.TestCase):
    def test_client_metrics_report_tail_and_fairness(self) -> None:
        logits = torch.tensor(
            [
                [5.0, 0.0],
                [0.0, 5.0],
                [5.0, 0.0],
                [5.0, 0.0],
            ]
        )
        labels = torch.tensor([0, 1, 0, 1])
        dataset = TensorDataset(logits, labels)
        model = torch.nn.Identity()
        rows, summary = evaluate_client_partitions(
            model,
            dataset,
            {
                0: np.asarray([0, 1], dtype=np.int64),
                1: np.asarray([2, 3], dtype=np.int64),
            },
            batch_size=2,
            device=torch.device("cpu"),
        )
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(summary.mean_client_accuracy, 0.75)
        self.assertAlmostEqual(summary.weighted_client_accuracy, 0.75)
        self.assertAlmostEqual(summary.worst_client_accuracy, 0.5)
        self.assertAlmostEqual(summary.best_client_accuracy, 1.0)
        self.assertAlmostEqual(summary.p10_client_accuracy, 0.55)
        self.assertAlmostEqual(summary.client_accuracy_std, 0.25)
        self.assertAlmostEqual(summary.client_accuracy_range, 0.5)
        self.assertAlmostEqual(summary.jain_accuracy_index, 0.9)


class RootCheckpointTests(unittest.TestCase):
    def test_server_writes_checkpoint_only_after_final_aggregation_call(self) -> None:
        previous_dir = os.environ.get("FL_ROOT_CHECKPOINT_DIR")
        previous_rounds = os.environ.get("FL_ROOT_CHECKPOINT_ROUNDS")
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.environ["FL_ROOT_CHECKPOINT_DIR"] = directory
                os.environ["FL_ROOT_CHECKPOINT_ROUNDS"] = "2"
                model = torch.nn.Linear(1, 1, bias=False)
                with torch.no_grad():
                    model.weight.zero_()
                server = Server(
                    model=model,
                    num_clients=1,
                    algorithm="fedavg",
                    device=torch.device("cpu"),
                )
                path = os.path.join(directory, "global_model_fedavg.pt")
                server.aggregate(
                    [
                        {
                            "delta": {"weight": torch.tensor([[0.5]])},
                            "num_samples": 1,
                        }
                    ]
                )
                self.assertFalse(os.path.isfile(path))

                # An empty final cohort leaves the model unchanged but still must
                # persist the final global state for post-run client evaluation.
                server.aggregate([])
                self.assertTrue(os.path.isfile(path))
                try:
                    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
                except TypeError:
                    checkpoint = torch.load(path, map_location="cpu")
                self.assertEqual(checkpoint["algorithm"], "fedavg")
                self.assertEqual(checkpoint["rounds_completed"], 2)
                self.assertAlmostEqual(
                    float(checkpoint["state_dict"]["weight"].item()), 0.5
                )
        finally:
            if previous_dir is None:
                os.environ.pop("FL_ROOT_CHECKPOINT_DIR", None)
            else:
                os.environ["FL_ROOT_CHECKPOINT_DIR"] = previous_dir
            if previous_rounds is None:
                os.environ.pop("FL_ROOT_CHECKPOINT_ROUNDS", None)
            else:
                os.environ["FL_ROOT_CHECKPOINT_ROUNDS"] = previous_rounds


if __name__ == "__main__":
    unittest.main()
