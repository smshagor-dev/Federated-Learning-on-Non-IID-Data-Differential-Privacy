"""Model construction. See docs/model-registry.md, docs/shared-backbone-local-head.md.

Two families are supported this phase:
- GroupNormCNN (models/networks.py, the existing legacy-prototype model):
  real-scale CNN, shared="features." / personalized="classifier." split.
- PersonalizableBridgeModel (new, below): a tiny two-flat-tensor model
  for exercising the coordinator's real transport path end-to-end
  (the CLI bridge's tensor manifest only carries small, explicitly-named
  flat tensors — see docs/coordinator-runtime.md), with a genuine
  shared/personalized split ("backbone" vs. "head").

ResNet-18 (with GroupNorm) is NOT implemented this phase: the task
allows it only "if already available or straightforward to add safely,"
and a correct GroupNorm-substituted ResNet-18 is enough new surface
(residual block restructuring, weight init, downsample-path GroupNorm
placement) that getting it right without a real training run to validate
against was judged not safe to add under this phase's time budget —
see docs/known-limitations.md. MLP and ViT are likewise not added: MLP
was never implemented (only registered as a "planned" tag in
models/registry.py) and ViT is explicitly out of scope unless already
stable, which it is not.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as functional


class PersonalizableBridgeModel(nn.Module):
    """Two flat parameter tensors, "backbone" (shared) and "head"
    (personalized) — sized so both fit the CLI bridge's small explicit
    tensor manifests (see docs/aggregation-manifests.md), with a real
    (if tiny) two-layer linear network behind them, not just placeholder
    weights.
    """

    def __init__(
        self,
        num_classes: int = 2,
        in_channels: int = 1,
        image_size: int = 4,
        embedding_dim: int = 4,
    ) -> None:
        super().__init__()
        self._num_classes = num_classes
        self._embedding_dim = embedding_dim
        self._in_features = in_channels * image_size * image_size
        self.backbone = nn.Parameter(torch.empty(embedding_dim * self._in_features))
        self.head = nn.Parameter(torch.empty(num_classes * embedding_dim))
        nn.init.uniform_(self.backbone, -0.1, 0.1)
        nn.init.uniform_(self.head, -0.1, 0.1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        backbone_matrix = self.backbone.view(self._embedding_dim, self._in_features)
        embedding = functional.relu(functional.linear(x.flatten(1), backbone_matrix))
        head_matrix = self.head.view(self._num_classes, self._embedding_dim)
        return functional.linear(embedding, head_matrix)


SHARED_PREFIXES = {
    "groupnorm_cnn": ["features."],
    "personalizable_bridge": ["backbone"],
}
PERSONALIZED_PREFIXES = {
    "groupnorm_cnn": ["classifier."],
    "personalizable_bridge": ["head"],
}


def build_model(
    name: str,
    *,
    num_classes: int = 2,
    in_channels: int = 1,
    image_size: int = 32,
) -> nn.Module:
    if name == "groupnorm_cnn":
        from models.networks import GroupNormCNN

        # models.networks is the legacy prototype package, covered by
        # pyproject.toml's `follow_imports = "skip"` override — GroupNormCNN
        # is therefore Any-typed to mypy, not actually untyped at runtime.
        return GroupNormCNN(num_classes=num_classes, in_channels=in_channels)  # type: ignore[no-any-return]
    if name == "personalizable_bridge":
        return PersonalizableBridgeModel(
            num_classes=num_classes, in_channels=in_channels, image_size=image_size
        )
    raise ValueError(
        f"unknown model '{name}'; available: groupnorm_cnn, personalizable_bridge"
    )
