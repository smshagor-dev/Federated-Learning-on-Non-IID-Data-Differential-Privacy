from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ModelDescriptor:
    name: str
    family: str
    normalization: str
    parameter_count: int | None = None
    tags: list[str] = field(default_factory=list)


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, ModelDescriptor] = {}

    def register(self, descriptor: ModelDescriptor) -> None:
        if descriptor.name in self._models:
            raise ValueError(f"model '{descriptor.name}' already registered")
        self._models[descriptor.name] = descriptor

    def get(self, name: str) -> ModelDescriptor:
        return self._models[name]

    def list_names(self) -> list[str]:
        return sorted(self._models)

    @classmethod
    def with_milestone_defaults(cls) -> ModelRegistry:
        registry = cls()
        registry.register(
            ModelDescriptor(
                name="groupnorm_cnn",
                family="cnn",
                normalization="groupnorm",
                tags=["legacy", "baseline"],
            )
        )
        registry.register(
            ModelDescriptor(
                name="mlp",
                family="mlp",
                normalization="none",
                tags=["planned"],
            )
        )
        registry.register(
            ModelDescriptor(
                name="resnet18_gn",
                family="resnet",
                normalization="groupnorm",
                tags=["planned"],
            )
        )
        registry.register(
            ModelDescriptor(
                name="mobilenetv3",
                family="mobilenet",
                normalization="batch-independent",
                tags=["planned"],
            )
        )
        registry.register(
            ModelDescriptor(
                name="vit_tiny",
                family="vit",
                normalization="layernorm",
                tags=["planned"],
            )
        )
        return registry
