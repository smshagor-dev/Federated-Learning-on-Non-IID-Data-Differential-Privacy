from __future__ import annotations

import unittest

from fl_platform.benchmark.matrix import (
    BenchmarkPartition,
    BenchmarkPlan,
    build_benchmark_plan,
)


class BenchmarkPlanTests(unittest.TestCase):
    def test_plan_expands_full_cartesian_matrix(self) -> None:
        plan = build_benchmark_plan(
            benchmark_id="matrix-a",
            datasets=("cifar10", "mnist"),
            algorithms=("fedavg", "fedprox"),
            partitions=(
                BenchmarkPartition("iid", "iid", {}),
                BenchmarkPartition("dirichlet-0.1", "dirichlet", {"alpha": 0.1}),
            ),
            target_epsilons=(None, 4.0),
            seeds=(11, 23, 37, 53, 71),
            rounds=50,
            runtime_identity="root-simulator",
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
            benchmark_id="matrix-b",
            datasets=("cifar10",),
            algorithms=("fedavg", "fedprox"),
            partitions=(BenchmarkPartition("iid", "iid", {}),),
            target_epsilons=(4.0,),
            seeds=(1, 2, 3, 4, 5),
            rounds=100,
            runtime_identity="root-simulator",
        )
        first = build_benchmark_plan(**kwargs)
        second = build_benchmark_plan(**kwargs)
        self.assertEqual(first.plan_hash(), second.plan_hash())
        self.assertEqual(first.expand(), second.expand())

    def test_fewer_than_five_seeds_fail(self) -> None:
        plan = BenchmarkPlan(
            benchmark_id="too-small",
            datasets=("cifar10",),
            algorithms=("fedavg",),
            partitions=(BenchmarkPartition("iid", "iid", {}),),
            target_epsilons=(None,),
            target_delta=1e-5,
            seeds=(1, 2, 3, 4),
            rounds=10,
            runtime_identity="root-simulator",
        )
        with self.assertRaisesRegex(ValueError, "at least 5 unique seeds"):
            plan.validate()

    def test_private_scaffold_is_rejected_for_root_runtime(self) -> None:
        plan = BenchmarkPlan(
            benchmark_id="scaffold-private",
            datasets=("cifar10",),
            algorithms=("scaffold",),
            partitions=(BenchmarkPartition("iid", "iid", {}),),
            target_epsilons=(4.0,),
            target_delta=1e-5,
            seeds=(1, 2, 3, 4, 5),
            rounds=10,
            runtime_identity="root-simulator",
        )
        with self.assertRaisesRegex(ValueError, "DP-enabled SCAFFOLD"):
            plan.validate()


if __name__ == "__main__":
    unittest.main()
