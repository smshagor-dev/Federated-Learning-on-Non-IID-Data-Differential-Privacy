"""Dataset loading and deterministic federated client partitioning.

Supported datasets:
  * MNIST
  * FashionMNIST
  * CIFAR-10
  * CIFAR-100

Supported partition strategies:
  * iid
  * dirichlet
  * pathological
  * quantity_skew
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, Mapping, Tuple

import matplotlib
import numpy as np
import torch
from torchvision import datasets, transforms

matplotlib.use("Agg")
import matplotlib.pyplot as plt

SUPPORTED_DATASETS = ("MNIST", "FASHIONMNIST", "CIFAR10", "CIFAR100")

_CIFAR10_MEAN = (0.4914, 0.4822, 0.4465)
_CIFAR10_STD = (0.2470, 0.2435, 0.2616)
_CIFAR100_MEAN = (0.5071, 0.4867, 0.4408)
_CIFAR100_STD = (0.2675, 0.2565, 0.2761)
_MNIST_MEAN = (0.1307,)
_MNIST_STD = (0.3081,)
_FASHION_MNIST_MEAN = (0.2860,)
_FASHION_MNIST_STD = (0.3530,)


def _grayscale_transform(mean: tuple[float], std: tuple[float]):
    return transforms.Compose(
        [
            transforms.Resize((32, 32)),
            transforms.ToTensor(),
            transforms.Normalize(mean, std),
        ]
    )


def get_dataset(
    name: str, data_root: str = "./data_raw"
) -> Tuple[torch.utils.data.Dataset, torch.utils.data.Dataset, int, int]:
    """Load a torchvision dataset for the root FL runtime."""
    name = name.upper().replace("-", "").replace("_", "")
    os.makedirs(data_root, exist_ok=True)

    if name == "CIFAR10":
        transform = transforms.Compose(
            [transforms.ToTensor(), transforms.Normalize(_CIFAR10_MEAN, _CIFAR10_STD)]
        )
        train_set = datasets.CIFAR10(
            data_root, train=True, download=True, transform=transform
        )
        test_set = datasets.CIFAR10(
            data_root, train=False, download=True, transform=transform
        )
        return train_set, test_set, 10, 3

    if name == "CIFAR100":
        transform = transforms.Compose(
            [
                transforms.ToTensor(),
                transforms.Normalize(_CIFAR100_MEAN, _CIFAR100_STD),
            ]
        )
        train_set = datasets.CIFAR100(
            data_root, train=True, download=True, transform=transform
        )
        test_set = datasets.CIFAR100(
            data_root, train=False, download=True, transform=transform
        )
        return train_set, test_set, 100, 3

    if name == "MNIST":
        transform = _grayscale_transform(_MNIST_MEAN, _MNIST_STD)
        train_set = datasets.MNIST(
            data_root, train=True, download=True, transform=transform
        )
        test_set = datasets.MNIST(
            data_root, train=False, download=True, transform=transform
        )
        return train_set, test_set, 10, 1

    if name == "FASHIONMNIST":
        transform = _grayscale_transform(_FASHION_MNIST_MEAN, _FASHION_MNIST_STD)
        train_set = datasets.FashionMNIST(
            data_root, train=True, download=True, transform=transform
        )
        test_set = datasets.FashionMNIST(
            data_root, train=False, download=True, transform=transform
        )
        return train_set, test_set, 10, 1

    raise ValueError(
        f"Unsupported dataset '{name}'. Choose one of {SUPPORTED_DATASETS}."
    )


def extract_targets(dataset: torch.utils.data.Dataset) -> np.ndarray:
    """Return a dataset label array as int64 NumPy values."""
    targets = dataset.targets
    if isinstance(targets, torch.Tensor):
        targets = targets.numpy()
    return np.asarray(targets, dtype=np.int64)


def _validate_partition_request(
    *, dataset_size: int, num_clients: int, min_partition_size: int
) -> None:
    if num_clients < 1:
        raise ValueError("num_clients must be >= 1")
    if min_partition_size < 1:
        raise ValueError("min_partition_size must be >= 1")
    if dataset_size < num_clients * min_partition_size:
        raise ValueError(
            "dataset is too small for the requested client count and minimum "
            f"partition size: samples={dataset_size}, clients={num_clients}, "
            f"minimum={min_partition_size}"
        )


def partition_iid(
    dataset: torch.utils.data.Dataset,
    num_clients: int,
    seed: int = 42,
    min_partition_size: int = 1,
) -> Dict[int, np.ndarray]:
    """Uniformly shuffle all samples and split them across clients."""
    dataset_size = len(dataset)
    _validate_partition_request(
        dataset_size=dataset_size,
        num_clients=num_clients,
        min_partition_size=min_partition_size,
    )
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(dataset_size)
    splits = np.array_split(shuffled, num_clients)
    if min(len(split) for split in splits) < min_partition_size:
        raise RuntimeError("IID split violated min_partition_size")
    return {
        client_id: np.sort(split.astype(np.int64, copy=False))
        for client_id, split in enumerate(splits)
    }


def partition_dirichlet(
    dataset: torch.utils.data.Dataset,
    num_clients: int,
    alpha: float,
    seed: int = 42,
    min_partition_size: int = 10,
    max_retries: int = 1000,
) -> Dict[int, np.ndarray]:
    """Create label-distribution skew with class-wise Dirichlet draws."""
    if alpha <= 0:
        raise ValueError("Dirichlet alpha must be > 0")
    targets = extract_targets(dataset)
    _validate_partition_request(
        dataset_size=len(targets),
        num_clients=num_clients,
        min_partition_size=min_partition_size,
    )
    classes = np.unique(targets)
    rng = np.random.default_rng(seed)

    for _ in range(max_retries):
        client_indices = [[] for _ in range(num_clients)]
        for class_id in classes:
            class_indices = np.where(targets == class_id)[0]
            rng.shuffle(class_indices)
            proportions = rng.dirichlet(np.full(num_clients, alpha))
            split_points = (np.cumsum(proportions)[:-1] * len(class_indices)).astype(int)
            for client_id, shard in enumerate(np.split(class_indices, split_points)):
                client_indices[client_id].extend(shard.tolist())

        if min(len(indices) for indices in client_indices) >= min_partition_size:
            return {
                client_id: np.asarray(sorted(indices), dtype=np.int64)
                for client_id, indices in enumerate(client_indices)
            }

    raise RuntimeError(
        f"Could not satisfy min_partition_size={min_partition_size} after "
        f"{max_retries} Dirichlet draws (alpha={alpha}, clients={num_clients})."
    )


def partition_pathological(
    dataset: torch.utils.data.Dataset,
    num_clients: int,
    classes_per_client: int = 2,
    seed: int = 42,
    min_partition_size: int = 1,
) -> Dict[int, np.ndarray]:
    """Shard samples by label so each client receives a small class subset."""
    if classes_per_client < 1:
        raise ValueError("classes_per_client must be >= 1")
    targets = extract_targets(dataset)
    _validate_partition_request(
        dataset_size=len(targets),
        num_clients=num_clients,
        min_partition_size=min_partition_size,
    )
    num_shards = num_clients * classes_per_client
    if num_shards > len(targets):
        raise ValueError("More shards requested than available samples")

    rng = np.random.default_rng(seed)
    sorted_indices = np.argsort(targets, kind="stable")
    shards = np.array_split(sorted_indices, num_shards)
    shard_order = rng.permutation(num_shards)

    result: Dict[int, np.ndarray] = {}
    for client_id in range(num_clients):
        picked = shard_order[
            client_id * classes_per_client : (client_id + 1) * classes_per_client
        ]
        indices = np.concatenate([shards[shard_id] for shard_id in picked])
        if len(indices) < min_partition_size:
            raise RuntimeError("pathological split violated min_partition_size")
        result[client_id] = np.asarray(sorted(indices.tolist()), dtype=np.int64)
    return result


def partition_quantity_skew(
    dataset: torch.utils.data.Dataset,
    num_clients: int,
    quantity_skew_sigma: float = 1.0,
    seed: int = 42,
    min_partition_size: int = 10,
) -> Dict[int, np.ndarray]:
    """Create client-size skew while keeping sample assignment label-agnostic."""
    if quantity_skew_sigma < 0.0:
        raise ValueError("quantity_skew_sigma must be >= 0")
    dataset_size = len(dataset)
    _validate_partition_request(
        dataset_size=dataset_size,
        num_clients=num_clients,
        min_partition_size=min_partition_size,
    )
    rng = np.random.default_rng(seed)
    weights = rng.lognormal(mean=0.0, sigma=quantity_skew_sigma, size=num_clients)
    probabilities = weights / weights.sum()
    remaining = dataset_size - num_clients * min_partition_size
    extras = rng.multinomial(remaining, probabilities)
    counts = extras + min_partition_size

    shuffled = rng.permutation(dataset_size)
    result: Dict[int, np.ndarray] = {}
    offset = 0
    for client_id, count in enumerate(counts.tolist()):
        next_offset = offset + int(count)
        result[client_id] = np.sort(
            shuffled[offset:next_offset].astype(np.int64, copy=False)
        )
        offset = next_offset
    if offset != dataset_size:
        raise RuntimeError("quantity-skew split did not consume the full dataset")
    return result


def client_label_histograms(
    client_dict: Mapping[int, np.ndarray], dataset: torch.utils.data.Dataset
) -> dict[str, dict[int, int]]:
    """Return exact label counts for every concrete client partition."""
    targets = extract_targets(dataset)
    histograms: dict[str, dict[int, int]] = {}
    for client_id, indices in sorted(client_dict.items()):
        labels, counts = np.unique(
            targets[np.asarray(indices, dtype=np.int64)], return_counts=True
        )
        histograms[f"client-{client_id}"] = {
            int(label): int(count)
            for label, count in zip(labels, counts, strict=True)
        }
    return histograms


def _largest_remainder_allocation(total: int, weights: np.ndarray) -> np.ndarray:
    """Allocate an integer total proportionally while preserving the exact sum."""
    if total < 0:
        raise ValueError("total must be >= 0")
    weights = np.asarray(weights, dtype=np.float64)
    if weights.ndim != 1 or len(weights) == 0:
        raise ValueError("weights must be a non-empty one-dimensional array")
    if np.any(weights < 0.0) or not np.all(np.isfinite(weights)):
        raise ValueError("weights must be finite and non-negative")
    weight_sum = float(weights.sum())
    probabilities = (
        np.full(len(weights), 1.0 / len(weights), dtype=np.float64)
        if weight_sum == 0.0
        else weights / weight_sum
    )
    exact = probabilities * total
    allocated = np.floor(exact).astype(np.int64)
    remainder = int(total - allocated.sum())
    if remainder:
        fractions = exact - allocated
        order = np.argsort(-fractions, kind="stable")
        allocated[order[:remainder]] += 1
    return allocated


def partition_evaluation_by_train_distribution(
    train_dataset: torch.utils.data.Dataset,
    train_client_dict: Mapping[int, np.ndarray],
    evaluation_dataset: torch.utils.data.Dataset,
    seed: int = 42,
) -> Dict[int, np.ndarray]:
    """Build held-out client partitions that mirror each training client's labels.

    For every label, the evaluation samples are allocated across clients in the
    same proportions observed for that label in the training partition. This
    preserves the realized client heterogeneity much more faithfully than
    independently re-running a partition generator on the test set.
    """
    if not train_client_dict:
        raise ValueError("train_client_dict must contain at least one client")

    client_ids = tuple(sorted(int(client_id) for client_id in train_client_dict))
    if client_ids != tuple(range(len(client_ids))):
        raise ValueError("client ids must be contiguous integers starting at zero")

    train_targets = extract_targets(train_dataset)
    evaluation_targets = extract_targets(evaluation_dataset)
    evaluation_classes = np.unique(evaluation_targets)
    rng = np.random.default_rng(seed)
    result_lists: list[list[int]] = [[] for _ in client_ids]

    for class_id in evaluation_classes:
        class_eval_indices = np.where(evaluation_targets == class_id)[0]
        rng.shuffle(class_eval_indices)
        train_counts = np.asarray(
            [
                np.count_nonzero(
                    train_targets[
                        np.asarray(train_client_dict[client_id], dtype=np.int64)
                    ]
                    == class_id
                )
                for client_id in client_ids
            ],
            dtype=np.float64,
        )
        allocations = _largest_remainder_allocation(len(class_eval_indices), train_counts)
        offset = 0
        for client_id, count in enumerate(allocations.tolist()):
            next_offset = offset + int(count)
            if count:
                result_lists[client_id].extend(
                    int(index) for index in class_eval_indices[offset:next_offset]
                )
            offset = next_offset
        if offset != len(class_eval_indices):
            raise RuntimeError("evaluation class allocation did not consume all samples")

    result = {
        client_id: np.asarray(sorted(indices), dtype=np.int64)
        for client_id, indices in enumerate(result_lists)
    }
    empty_clients = [client_id for client_id, indices in result.items() if len(indices) == 0]
    if empty_clients:
        raise RuntimeError(
            "matched held-out evaluation produced empty client partitions; "
            f"reduce client count or use a larger evaluation set: {empty_clients}"
        )

    assigned = np.concatenate([result[client_id] for client_id in client_ids])
    if len(assigned) != len(evaluation_dataset):
        raise RuntimeError("evaluation partition did not assign every sample exactly once")
    if len(np.unique(assigned)) != len(assigned):
        raise RuntimeError("evaluation partition assigned at least one sample twice")
    return result


def partition_fingerprint(client_dict: Mapping[int, np.ndarray]) -> str:
    """Hash the exact client-to-index assignment."""
    payload = {
        str(client_id): [int(index) for index in np.asarray(indices, dtype=np.int64)]
        for client_id, indices in sorted(client_dict.items())
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plot_distribution(
    client_dict: Dict[int, np.ndarray],
    dataset: torch.utils.data.Dataset,
    num_classes: int,
    save_path: str = "results/distribution.png",
) -> str:
    """Save a stacked bar chart of per-client class counts."""
    targets = extract_targets(dataset)
    num_clients = len(client_dict)

    counts = np.zeros((num_clients, num_classes), dtype=np.int64)
    for client_id, indices in client_dict.items():
        labels, frequency = np.unique(targets[indices], return_counts=True)
        counts[client_id, labels] = frequency

    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    cmap = plt.get_cmap("tab10")
    figure, axis = plt.subplots(
        figsize=(max(8, num_clients * 0.5), 5),
        dpi=200,
    )
    bottom = np.zeros(num_clients)
    x_values = np.arange(num_clients)
    for class_id in range(num_classes):
        axis.bar(
            x_values,
            counts[:, class_id],
            bottom=bottom,
            color=cmap(class_id % 10),
            width=0.8,
            label=f"class {class_id}",
        )
        bottom += counts[:, class_id]

    axis.set_xlabel("Client ID")
    axis.set_ylabel("Number of samples")
    axis.set_title("Per-client class distribution")
    axis.set_xticks(x_values)
    if num_classes <= 20:
        axis.legend(ncol=min(5, num_classes), fontsize=8, loc="upper right")
    figure.tight_layout()
    figure.savefig(save_path)
    plt.close(figure)
    return save_path
