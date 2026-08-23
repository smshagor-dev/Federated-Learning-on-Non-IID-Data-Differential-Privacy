from __future__ import annotations

import unittest

from fl_platform.benchmark.results import (
    BenchmarkObservation,
    compare_algorithms,
    summarize_observations,
    validate_observations,
)


def observation(
    *,
    algorithm: str,
    seed: int,
    value: float,
    partition_hash: str | None = None,
) -> BenchmarkObservation:
    return BenchmarkObservation(
        benchmark_id="benchmark-a",
        dataset_id="cifar10",
        partition_id="dirichlet-0.1",
        partition_hash=partition_hash or f"partition-{seed}",
        algorithm_id=algorithm,
        target_epsilon=4.0,
        target_delta=1e-5,
        seed=seed,
        metric="global_accuracy",
        value=value,
        runtime_identity="root-simulator",
        commit_sha="0123456789abcdef",
        specification_hash=f"spec-{algorithm}-{seed}",
    )


class BenchmarkResultTests(unittest.TestCase):
    def test_summaries_aggregate_across_seed_specific_partition_hashes(self) -> None:
        rows = []
        for seed, value in enumerate((0.70, 0.71, 0.69, 0.72, 0.70), start=1):
            rows.append(observation(algorithm="fedavg", seed=seed, value=value))
        for seed, value in enumerate((0.72, 0.73, 0.72, 0.74, 0.75), start=1):
            rows.append(observation(algorithm="fedprox", seed=seed, value=value))
        summaries = summarize_observations(rows, bootstrap_samples=500)
        self.assertEqual(len(summaries), 2)
        by_algorithm = {row.algorithm_id: row for row in summaries}
        self.assertEqual(by_algorithm["fedavg"].n, 5)
        self.assertEqual(by_algorithm["fedavg"].partition_hash_count, 5)
        self.assertEqual(by_algorithm["fedprox"].n, 5)
        self.assertGreater(by_algorithm["fedprox"].mean, by_algorithm["fedavg"].mean)

    def test_paired_comparison_uses_matched_seeds_and_partition_hashes(self) -> None:
        rows = []
        baseline = (0.70, 0.71, 0.69, 0.72, 0.70)
        candidate = (0.72, 0.73, 0.72, 0.74, 0.75)
        for seed, value in enumerate(baseline, start=1):
            rows.append(observation(algorithm="fedavg", seed=seed, value=value))
        for seed, value in enumerate(candidate, start=1):
            rows.append(observation(algorithm="fedprox", seed=seed, value=value))
        comparisons = compare_algorithms(
            rows,
            baseline_algorithm="fedavg",
            bootstrap_samples=500,
        )
        self.assertEqual(len(comparisons), 1)
        comparison = comparisons[0]
        self.assertEqual(comparison.n, 5)
        self.assertGreater(comparison.mean_difference, 0.0)
        self.assertGreaterEqual(comparison.p_value_holm, comparison.p_value)
        self.assertTrue(comparison.partition_hashes_digest)

    def test_different_partition_for_same_seed_is_rejected(self) -> None:
        rows = []
        for seed in range(1, 6):
            rows.append(observation(algorithm="fedavg", seed=seed, value=0.70))
            rows.append(
                observation(
                    algorithm="fedprox",
                    seed=seed,
                    value=0.72,
                    partition_hash="wrong-hash" if seed == 3 else f"partition-{seed}",
                )
            )
        with self.assertRaisesRegex(ValueError, "different exact partitions"):
            compare_algorithms(
                rows,
                baseline_algorithm="fedavg",
                bootstrap_samples=500,
            )

    def test_duplicate_observations_fail_closed(self) -> None:
        row = observation(algorithm="fedavg", seed=1, value=0.7)
        with self.assertRaisesRegex(ValueError, "duplicate benchmark observation"):
            validate_observations([row, row], minimum_replicates=1)


if __name__ == "__main__":
    unittest.main()
