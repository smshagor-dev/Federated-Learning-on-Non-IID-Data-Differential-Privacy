from __future__ import annotations

import unittest

from fl_platform.research.benchmark import (
    BenchmarkPartition,
    BenchmarkPlan,
    build_publication_plan,
)
from fl_platform.research.specification import PartitionStrategy


class BenchmarkPlanTests(unittest.TestCase):
    def test_publication_plan_expands_full_cartesian_matrix(self) -> None:
        partitions = (
            BenchmarkPartition("iid", PartitionStrategy.IID, {}),
            BenchmarkPartition(
                "dirichlet-0.1", PartitionStrategy.DIRICHLET, {"alpha": 0.1}
            ),
        )
        plan = build_publication_plan(
            benchmark_id="paper-a",
            datasets=("cifar10", "femnist"),
            algorithms=("fedavg", "fedprox"),
            partitions=partitions,
            target_epsilons=(None, 4.0),
            seeds=(11, 23, 37, 53, 71),
            rounds=50,
        )
        cells = plan.expand()
        self.assertEqual(len(cells), 2 * 2 * 2 * 2 * 5)
        self.assertEqual(len({cell.cell_id for cell in cells}), len(cells))

        first_condition = cells[0].condition_id
        matching = [cell for cell in cells if cell.condition_id == first_condition]
        self.assertEqual(len(matching), 5)
        self.assertEqual({cell.seed for cell in matching}, {11, 23, 37, 53, 71})

    def test_plan_hash_and_cell_ids_are_deterministic(self) -> None:
        kwargs = dict(
            benchmark_id="paper-b",
            datasets=("cifar10",),
            algorithms=("fedavg", "fedprox"),
            partitions=(
                BenchmarkPartition("iid", PartitionStrategy.IID, {}),
            ),
            target_epsilons=(4.0,),
            seeds=(1, 2, 3, 4, 5),
            rounds=100,
        )
        first = build_publication_plan(**kwargs)
        second = build_publication_plan(**kwargs)
        self.assertEqual(first.plan_hash(), second.plan_hash())
        self.assertEqual(first.expand(), second.expand())

    def test_fewer_than_five_seeds_fail_publication_gate(self) -> None:
        plan = BenchmarkPlan(
            benchmark_id="too-small",
            datasets=("cifar10",),
            algorithms=("fedavg",),
            partitions=(BenchmarkPartition("iid", PartitionStrategy.IID, {}),),
            target_epsilons=(None,),
            target_delta=1e-5,
            seeds=(1, 2, 3, 4),
            rounds=10,
            runtime_identity="root-simulator",
        )
        with self.assertRaisesRegex(ValueError, "at least 5 unique seeds"):
            plan.validate()

    def test_invalid_runtime_identity_is_rejected(self) -> None:
        plan = BenchmarkPlan(
            benchmark_id="bad-runtime",
            datasets=("cifar10",),
            algorithms=("fedavg",),
            partitions=(BenchmarkPartition("iid", PartitionStrategy.IID, {}),),
            target_epsilons=(None,),
            target_delta=1e-5,
            seeds=(1, 2, 3, 4, 5),
            rounds=10,
            runtime_identity="unknown",
        )
        with self.assertRaisesRegex(ValueError, "runtime_identity"):
            plan.validate()


if __name__ == "__main__":
    unittest.main()
