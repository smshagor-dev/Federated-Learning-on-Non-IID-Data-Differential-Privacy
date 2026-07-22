"""Non-IID data loading and client partitioning.

Provides:
  * get_dataset            -- CIFAR-10 / MNIST loaders with normalization
  * partition_dirichlet    -- label-skew split via a Dirichlet(alpha) prior
  * partition_pathological -- shard-based k-classes-per-client split
  * plot_distribution      -- stacked bar chart of the class counts per client
"""

from __future__ import annotations

import os
from typing import Dict, Tuple

import numpy as np
import matplotlib

matplotlib.use("Agg")  # headless-safe backend
import matplotlib.pyplot as plt
import torch
from torchvision import datasets, transforms

# Per-channel statistics computed on the training splits.
_CIFAR10_MEAN = (0.4914, 0.4822, 0.4465)
_CIFAR10_STD = (0.2470, 0.2435, 0.2616)
_MNIST_MEAN = (0.1307,)
_MNIST_STD = (0.3081,)


def get_dataset(
    name: str, data_root: str = "./data_raw"
) -> Tuple[torch.utils.data.Dataset, torch.utils.data.Dataset, int, int]:
    """Load a torchvision dataset ready for the FL simulator.

    Args:
        name: "CIFAR10" or "MNIST" (case-insensitive).
        data_root: download/cache directory.

    Returns:
        (train_set, test_set, num_classes, in_channels)
    """
    name = name.upper()
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

    if name == "MNIST":
        # Resize to 32x32 so the same CNN architecture handles both datasets.
        transform = transforms.Compose(
            [
                transforms.Resize((32, 32)),
                transforms.ToTensor(),
                transforms.Normalize(_MNIST_MEAN, _MNIST_STD),
            ]
        )
        train_set = datasets.MNIST(
            data_root, train=True, download=True, transform=transform
        )
        test_set = datasets.MNIST(
            data_root, train=False, download=True, transform=transform
        )
        return train_set, test_set, 10, 1

    raise ValueError(f"Unsupported dataset '{name}'. Choose 'CIFAR10' or 'MNIST'.")


def _extract_targets(dataset: torch.utils.data.Dataset) -> np.ndarray:
    """Return the label array of a torchvision dataset as int64 numpy."""
    targets = dataset.targets
    if isinstance(targets, torch.Tensor):
        targets = targets.numpy()
    return np.asarray(targets, dtype=np.int64)


def partition_dirichlet(
    dataset: torch.utils.data.Dataset,
    num_clients: int,
    alpha: float,
    seed: int = 42,
    min_partition_size: int = 10,
    max_retries: int = 1000,
) -> Dict[int, np.ndarray]:
    """Split sample indices among clients with a Dirichlet(alpha) label prior.

    For every class c we draw p ~ Dir(alpha * 1_K) over the K clients and give
    client k a fraction p_k of the class-c samples. Small alpha (e.g. 0.1)
    yields extreme label skew; large alpha (e.g. 100) approaches IID.

    The draw is repeated until every client owns at least
    ``min_partition_size`` samples so that local DataLoaders are never empty.

    Returns:
        dict: client_id -> sorted np.ndarray of sample indices.
    """
    if num_clients < 1:
        raise ValueError("num_clients must be >= 1")
    if alpha <= 0:
        raise ValueError("Dirichlet alpha must be > 0")

    targets = _extract_targets(dataset)
    classes = np.unique(targets)
    rng = np.random.default_rng(seed)

    for attempt in range(max_retries):
        client_indices = [[] for _ in range(num_clients)]
        for c in classes:
            idx_c = np.where(targets == c)[0]
            rng.shuffle(idx_c)
            proportions = rng.dirichlet(np.full(num_clients, alpha))
            # Convert proportions to split points along the shuffled class array.
            split_points = (np.cumsum(proportions)[:-1] * len(idx_c)).astype(int)
            for client_id, shard in enumerate(np.split(idx_c, split_points)):
                client_indices[client_id].extend(shard.tolist())

        smallest = min(len(ci) for ci in client_indices)
        if smallest >= min_partition_size:
            return {
                cid: np.array(sorted(client_indices[cid]), dtype=np.int64)
                for cid in range(num_clients)
            }

    raise RuntimeError(
        f"Could not satisfy min_partition_size={min_partition_size} after "
        f"{max_retries} Dirichlet draws (alpha={alpha}, clients={num_clients}). "
        "Increase alpha or lower min_partition_size."
    )


def partition_pathological(
    dataset: torch.utils.data.Dataset,
    num_clients: int,
    classes_per_client: int = 2,
    seed: int = 42,
) -> Dict[int, np.ndarray]:
    """Pathological non-IID split (McMahan et al., 2017).

    Samples are sorted by label and cut into ``num_clients * classes_per_client``
    equally sized shards; each client receives ``classes_per_client`` random
    shards, so it sees at most that many distinct classes.
    """
    targets = _extract_targets(dataset)
    num_shards = num_clients * classes_per_client
    if num_shards > len(targets):
        raise ValueError("More shards requested than available samples.")

    rng = np.random.default_rng(seed)
    sorted_idx = np.argsort(targets, kind="stable")
    shards = np.array_split(sorted_idx, num_shards)
    shard_order = rng.permutation(num_shards)

    client_dict: Dict[int, np.ndarray] = {}
    for cid in range(num_clients):
        picked = shard_order[cid * classes_per_client : (cid + 1) * classes_per_client]
        idx = np.concatenate([shards[s] for s in picked])
        client_dict[cid] = np.array(sorted(idx.tolist()), dtype=np.int64)
    return client_dict


def plot_distribution(
    client_dict: Dict[int, np.ndarray],
    dataset: torch.utils.data.Dataset,
    num_classes: int,
    save_path: str = "results/distribution.png",
) -> str:
    """Save a stacked bar chart of per-client class counts.

    Returns the path the figure was written to.
    """
    targets = _extract_targets(dataset)
    num_clients = len(client_dict)

    counts = np.zeros((num_clients, num_classes), dtype=np.int64)
    for cid, indices in client_dict.items():
        labels, freq = np.unique(targets[indices], return_counts=True)
        counts[cid, labels] = freq

    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    cmap = plt.get_cmap("tab10")
    fig, ax = plt.subplots(figsize=(max(8, num_clients * 0.5), 5), dpi=200)
    bottom = np.zeros(num_clients)
    x = np.arange(num_clients)
    for c in range(num_classes):
        ax.bar(
            x,
            counts[:, c],
            bottom=bottom,
            color=cmap(c % 10),
            width=0.8,
            label=f"class {c}",
        )
        bottom += counts[:, c]

    ax.set_xlabel("Client ID")
    ax.set_ylabel("Number of samples")
    ax.set_title("Per-client class distribution (Non-IID partition)")
    ax.set_xticks(x)
    ax.legend(ncol=5, fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)
    return save_path
