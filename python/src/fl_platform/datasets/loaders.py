"""Real MNIST/CIFAR-10 loading via torchvision (the Algorithm Expansion phase, Work
Package I). Genuine code, never called by the automated test suite —
per the task's explicit "automated tests must not download datasets"
instruction, only dataset_loader.py's SyntheticImageDataset is exercised
by pytest. These loaders are the natural on-demand path for a real
training run outside of CI (see docs/dataset-registry.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from torch.utils.data import Dataset


def load_mnist(
    root: str | Path, *, train: bool = True, download: bool = True
) -> Dataset[Any]:
    # torchvision ships no py.typed marker; ignored via pyproject.toml's
    # `[[tool.mypy.overrides]]` for module "torchvision"/"torchvision.*"
    # (same pattern as grpc — see GrpcCoordinatorClient's constructor).
    import torchvision
    from torchvision import transforms

    transform = transforms.Compose(
        [transforms.ToTensor(), transforms.Normalize((0.1307,), (0.3081,))]
    )
    # torchvision is untyped (see above), so this constructor call's
    # static type is Any regardless of the declared Dataset[Any] return —
    # a genuine consequence of an untyped dependency, not a real bug.
    return torchvision.datasets.MNIST(  # type: ignore[no-any-return]
        root=str(root), train=train, download=download, transform=transform
    )


def load_cifar10(
    root: str | Path, *, train: bool = True, download: bool = True
) -> Dataset[Any]:
    import torchvision
    from torchvision import transforms

    transform = transforms.Compose(
        [
            transforms.ToTensor(),
            transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2470, 0.2435, 0.2616)),
        ]
    )
    return torchvision.datasets.CIFAR10(  # type: ignore[no-any-return]
        root=str(root), train=train, download=download, transform=transform
    )


DATASET_LOADERS = {
    "mnist": load_mnist,
    "cifar10": load_cifar10,
}
