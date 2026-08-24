from __future__ import annotations

import pytest
import torch
from fl_platform.worker.dataset_loader import (
    clear_verified_partition_references,
    load_partition,
    manifest_for_client,
    register_verified_partition_reference,
)


@pytest.fixture(autouse=True)
def _clear_verified_references() -> None:
    clear_verified_partition_references()
    yield
    clear_verified_partition_references()


def _reference(
    strategy: str,
    *,
    alpha: float = 0.0,
    classes_per_client: int = 0,
    quantity_skew_sigma: float = 0.0,
    min_client_size: int = 0,
    seed: int = 17,
) -> str:
    return (
        "fl-partition-v1://synthetic?dataset=CIFAR100"
        f"&strategy={strategy}"
        f"&alpha={alpha}"
        f"&classes_per_client={classes_per_client}"
        f"&quantity_skew_sigma={quantity_skew_sigma}"
        f"&min_client_size={min_client_size}"
        f"&seed={seed}"
    )


def test_legacy_reference_remains_iid_and_reproducible() -> None:
    first = manifest_for_client(
        "synthetic:client-a", "client-a", 9, sample_count=32
    )
    second = manifest_for_client(
        "synthetic:client-a", "client-a", 9, sample_count=32
    )
    assert first.partition_strategy == "iid"
    assert first.seed == second.seed
    dataset_a, _ = load_partition(first)
    dataset_b, _ = load_partition(second)
    assert torch.equal(dataset_a[0][0], dataset_b[0][0])
    assert [int(dataset_a[index][1]) for index in range(8)] == [0, 1, 2, 3] * 2
    assert int(dataset_a[0][1]) == int(dataset_b[0][1])


def test_verified_reference_overrides_legacy_task_runner_reference() -> None:
    register_verified_partition_reference(
        "client-a", _reference("pathological", classes_per_client=2)
    )
    manifest = manifest_for_client(
        "synthetic:client-a", "client-a", 999, sample_count=64
    )
    manifest.num_classes = 10
    dataset, _ = load_partition(manifest)
    labels = {int(dataset[index][1]) for index in range(len(dataset))}
    assert manifest.dataset_id == "CIFAR100"
    assert manifest.partition_strategy == "pathological"
    assert len(labels) <= 2


def test_dirichlet_reference_is_deterministic_per_client() -> None:
    reference = _reference("dirichlet", alpha=0.1, seed=42)
    register_verified_partition_reference("client-a", reference)
    first = manifest_for_client(
        "synthetic:client-a", "client-a", 1, sample_count=128
    )
    first.num_classes = 8
    dataset_a, _ = load_partition(first)

    clear_verified_partition_references()
    register_verified_partition_reference("client-a", reference)
    second = manifest_for_client(
        "synthetic:client-a", "client-a", 9999, sample_count=128
    )
    second.num_classes = 8
    dataset_b, _ = load_partition(second)

    labels_a = torch.tensor(
        [int(dataset_a[index][1]) for index in range(len(dataset_a))]
    )
    labels_b = torch.tensor(
        [int(dataset_b[index][1]) for index in range(len(dataset_b))]
    )
    assert first.seed == second.seed
    assert torch.equal(labels_a, labels_b)


def test_quantity_skew_changes_local_count_but_respects_minimum() -> None:
    register_verified_partition_reference(
        "client-a",
        _reference(
            "quantity_skew",
            quantity_skew_sigma=1.0,
            min_client_size=19,
        ),
    )
    manifest = manifest_for_client(
        "synthetic:client-a", "client-a", 0, sample_count=32
    )
    assert manifest.partition_strategy == "quantity_skew"
    assert manifest.sample_count >= 19
    assert manifest.sample_count <= 32 * 32


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        (_reference("dirichlet", alpha=0.0), "alpha"),
        (_reference("pathological", classes_per_client=0), "classes_per_client"),
        (
            _reference("quantity_skew", quantity_skew_sigma=0.0),
            "quantity_skew_sigma",
        ),
        (_reference("unknown"), "unsupported partition strategy"),
    ],
)
def test_invalid_canonical_partition_parameters_fail_closed(
    reference: str, message: str
) -> None:
    register_verified_partition_reference("client-a", reference)
    with pytest.raises(ValueError, match=message):
        manifest_for_client("synthetic:client-a", "client-a", 0)
