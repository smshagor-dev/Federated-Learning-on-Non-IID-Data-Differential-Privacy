from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from data.partitioner import (
    partition_iid,
    partition_quantity_skew,
)
from utils.partition_metrics import (
    client_label_histograms,
    partition_hash,
    write_partition_artifacts,
)


class DummyDataset:
    def __init__(self, labels: list[int]) -> None:
        self.targets = np.asarray(labels, dtype=np.int64)

    def __len__(self) -> int:
        return int(len(self.targets))


class PartitioningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = DummyDataset([index % 10 for index in range(1000)])

    def assert_exact_cover(self, partitions: dict[int, np.ndarray]) -> None:
        flattened = np.concatenate(list(partitions.values()))
        self.assertEqual(len(flattened), len(self.dataset))
        self.assertEqual(len(np.unique(flattened)), len(self.dataset))
        self.assertEqual(set(flattened.tolist()), set(range(len(self.dataset))))

    def test_iid_partition_is_deterministic_and_complete(self) -> None:
        first = partition_iid(
            self.dataset,
            num_clients=10,
            seed=17,
            min_partition_size=10,
        )
        second = partition_iid(
            self.dataset,
            num_clients=10,
            seed=17,
            min_partition_size=10,
        )
        self.assert_exact_cover(first)
        self.assertEqual(partition_hash(first), partition_hash(second))
        self.assertEqual({len(indices) for indices in first.values()}, {100})

    def test_quantity_skew_is_deterministic_complete_and_non_uniform(self) -> None:
        first = partition_quantity_skew(
            self.dataset,
            num_clients=10,
            quantity_skew_sigma=1.5,
            seed=23,
            min_partition_size=10,
        )
        second = partition_quantity_skew(
            self.dataset,
            num_clients=10,
            quantity_skew_sigma=1.5,
            seed=23,
            min_partition_size=10,
        )
        self.assert_exact_cover(first)
        self.assertEqual(partition_hash(first), partition_hash(second))
        counts = [len(indices) for indices in first.values()]
        self.assertGreater(max(counts), min(counts))
        self.assertGreaterEqual(min(counts), 10)

    def test_partition_artifacts_persist_exact_indices_and_metrics(self) -> None:
        partitions = partition_iid(
            self.dataset,
            num_clients=10,
            seed=31,
            min_partition_size=10,
        )
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, indices_path, manifest = write_partition_artifacts(
                client_dict=partitions,
                dataset=self.dataset,
                dataset_name="dummy",
                strategy="iid",
                seed=31,
                parameters={},
                output_dir=directory,
            )
            self.assertTrue(Path(manifest_path).is_file())
            self.assertTrue(Path(indices_path).is_file())
            persisted = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            self.assertEqual(persisted["partition_hash"], partition_hash(partitions))
            self.assertEqual(persisted["heterogeneity"]["client_count"], 10)
            self.assertEqual(persisted["heterogeneity"]["total_samples"], 1000)
            self.assertEqual(manifest["partition_hash"], persisted["partition_hash"])
            histograms = client_label_histograms(partitions, self.dataset)
            self.assertEqual(len(histograms), 10)


if __name__ == "__main__":
    unittest.main()
