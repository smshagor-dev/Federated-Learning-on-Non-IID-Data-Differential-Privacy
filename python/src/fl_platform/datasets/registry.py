from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DatasetDescriptor:
    name: str
    modality: str
    num_classes: int | None = None
    supports_partitioning: list[str] = field(default_factory=list)


class DatasetRegistry:
    def __init__(self) -> None:
        self._datasets: dict[str, DatasetDescriptor] = {}

    def register(self, descriptor: DatasetDescriptor) -> None:
        if descriptor.name in self._datasets:
            raise ValueError(f"dataset '{descriptor.name}' already registered")
        self._datasets[descriptor.name] = descriptor

    def get(self, name: str) -> DatasetDescriptor:
        return self._datasets[name]

    def list_names(self) -> list[str]:
        return sorted(self._datasets)

    @classmethod
    def with_milestone_defaults(cls) -> "DatasetRegistry":
        registry = cls()
        partitioning = ["iid", "dirichlet", "pathological"]
        for name, classes in [
            ("mnist", 10),
            ("fashion_mnist", 10),
            ("cifar10", 10),
            ("cifar100", 100),
            ("femnist", 62),
            ("tiny_imagenet", 200),
        ]:
            registry.register(
                DatasetDescriptor(
                    name=name,
                    modality="image",
                    num_classes=classes,
                    supports_partitioning=partitioning,
                )
            )
        registry.register(
            DatasetDescriptor(
                name="custom_image_folder",
                modality="image",
                supports_partitioning=["iid", "dirichlet", "pathological", "quantity_skew"],
            )
        )
        registry.register(
            DatasetDescriptor(
                name="custom_manifest_dataset",
                modality="manifest",
                supports_partitioning=["iid", "dirichlet", "pathological", "quantity_skew"],
            )
        )
        return registry
