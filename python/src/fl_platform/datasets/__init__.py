"""Dataset registry, partitioning, and loader public API.

Package exports are lazy so lightweight federated-shard validation does not
pull in NumPy or Torch-backed centralized dataset loaders unless those symbols
are actually requested.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dataset_registry import (
        DatasetRegistryEntry,
        DatasetRegistryError,
        DatasetStatus,
        FilesystemDatasetRegistry,
    )
    from .federated_loaders import (
        DatasetShardSpec,
        FederatedDatasetBundle,
        FederatedDatasetError,
        FederatedDatasetManifest,
        FederatedUserPartition,
        load_federated_leaf_dataset,
        load_femnist_leaf,
        load_sent140_leaf,
        load_shakespeare_leaf,
        sha256_file,
    )
    from .loaders import DATASET_LOADERS, load_cifar10, load_mnist
    from .partitioning import (
        PartitionError,
        PartitionManifestRecord,
        create_dirichlet_partition,
        create_iid_partition,
        create_pathological_partition,
        create_quantity_skew_partition,
    )
    from .registry import DatasetDescriptor, DatasetRegistry

_LAZY_EXPORTS = {
    "DatasetRegistryEntry": ("dataset_registry", "DatasetRegistryEntry"),
    "DatasetRegistryError": ("dataset_registry", "DatasetRegistryError"),
    "DatasetStatus": ("dataset_registry", "DatasetStatus"),
    "FilesystemDatasetRegistry": ("dataset_registry", "FilesystemDatasetRegistry"),
    "DatasetShardSpec": ("federated_loaders", "DatasetShardSpec"),
    "FederatedDatasetBundle": ("federated_loaders", "FederatedDatasetBundle"),
    "FederatedDatasetError": ("federated_loaders", "FederatedDatasetError"),
    "FederatedDatasetManifest": ("federated_loaders", "FederatedDatasetManifest"),
    "FederatedUserPartition": ("federated_loaders", "FederatedUserPartition"),
    "load_federated_leaf_dataset": (
        "federated_loaders",
        "load_federated_leaf_dataset",
    ),
    "load_femnist_leaf": ("federated_loaders", "load_femnist_leaf"),
    "load_sent140_leaf": ("federated_loaders", "load_sent140_leaf"),
    "load_shakespeare_leaf": ("federated_loaders", "load_shakespeare_leaf"),
    "sha256_file": ("federated_loaders", "sha256_file"),
    "DATASET_LOADERS": ("loaders", "DATASET_LOADERS"),
    "load_cifar10": ("loaders", "load_cifar10"),
    "load_mnist": ("loaders", "load_mnist"),
    "PartitionError": ("partitioning", "PartitionError"),
    "PartitionManifestRecord": ("partitioning", "PartitionManifestRecord"),
    "create_dirichlet_partition": ("partitioning", "create_dirichlet_partition"),
    "create_iid_partition": ("partitioning", "create_iid_partition"),
    "create_pathological_partition": (
        "partitioning",
        "create_pathological_partition",
    ),
    "create_quantity_skew_partition": (
        "partitioning",
        "create_quantity_skew_partition",
    ),
    "DatasetDescriptor": ("registry", "DatasetDescriptor"),
    "DatasetRegistry": ("registry", "DatasetRegistry"),
}


def __getattr__(name: str) -> object:
    try:
        module_name, symbol_name = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(f"{__name__}.{module_name}"), symbol_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()).union(_LAZY_EXPORTS))


__all__ = sorted(_LAZY_EXPORTS)
