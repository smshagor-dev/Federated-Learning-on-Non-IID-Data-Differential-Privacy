"""v3 workload catalog with explicit implementation maturity."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Workload:
    name: str
    modality: str
    naturally_federated: bool
    status: str
    license_note: str


WORKLOADS: dict[str, Workload] = {
    "mnist": Workload(
        "mnist",
        "image",
        False,
        "validated-v2",
        "torchvision dataset terms apply",
    ),
    "fashion_mnist": Workload(
        "fashion_mnist",
        "image",
        False,
        "validated-v2",
        "torchvision dataset terms apply",
    ),
    "cifar10": Workload(
        "cifar10",
        "image",
        False,
        "validated-v2",
        "CIFAR terms apply",
    ),
    "cifar100": Workload(
        "cifar100",
        "image",
        False,
        "validated-v2",
        "CIFAR terms apply",
    ),
    "femnist": Workload(
        "femnist",
        "image",
        True,
        "experimental-manifest",
        "LEAF/FEMNIST source terms must be verified",
    ),
    "shakespeare": Workload(
        "shakespeare",
        "text",
        True,
        "experimental-manifest",
        "LEAF corpus provenance must be archived",
    ),
    "sent140": Workload(
        "sent140",
        "text",
        True,
        "experimental-manifest",
        "dataset terms and redistribution constraints must be verified",
    ),
}


def get_workload(name: str, *, require_validated: bool = False) -> Workload:
    normalized = name.lower()
    workload = WORKLOADS.get(normalized)
    if workload is None:
        raise ValueError(f"unknown workload: {name}")
    if require_validated and not workload.status.startswith("validated"):
        raise ValueError(f"workload {name} is not release-validated")
    return workload


__all__ = ["WORKLOADS", "Workload", "get_workload"]
