"""Dataset registry, partitioning, and real dataset loaders. See
docs/dataset-registry.md."""

from .dataset_registry import (
    DatasetRegistryEntry,
    DatasetRegistryError,
    DatasetStatus,
    FilesystemDatasetRegistry,
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

__all__ = [
    "DATASET_LOADERS",
    "DatasetDescriptor",
    "DatasetRegistry",
    "DatasetRegistryEntry",
    "DatasetRegistryError",
    "DatasetStatus",
    "FilesystemDatasetRegistry",
    "PartitionError",
    "PartitionManifestRecord",
    "create_dirichlet_partition",
    "create_iid_partition",
    "create_pathological_partition",
    "create_quantity_skew_partition",
    "load_cifar10",
    "load_mnist",
]
