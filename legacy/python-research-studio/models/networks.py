"""FL-optimized model architectures.

BatchNorm is deliberately avoided: its running statistics diverge across
non-IID clients and are corrupted by weighted parameter averaging.
GroupNorm normalizes per-sample, is batch-size independent, and is the
standard replacement for federated settings (Hsieh et al., 2020).
"""

from __future__ import annotations

import torch
import torch.nn as nn


class GroupNormCNN(nn.Module):
    """Lightweight CNN for 32x32 inputs (CIFAR-10 / resized MNIST).

    Architecture:
        [Conv3x3(32) - GN - ReLU - Conv3x3(64) - GN - ReLU - MaxPool2]  -> 16x16
        [Conv3x3(128) - GN - ReLU - MaxPool2]                           -> 8x8
        [Flatten - FC(256) - ReLU - Dropout - FC(num_classes)]
    """

    def __init__(
        self,
        num_classes: int = 10,
        in_channels: int = 3,
        group_norm_groups: int = 2,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        g = group_norm_groups

        self.features = nn.Sequential(
            nn.Conv2d(in_channels, 32, kernel_size=3, padding=1, bias=False),
            nn.GroupNorm(num_groups=g, num_channels=32),
            nn.ReLU(inplace=True),
            nn.Conv2d(32, 64, kernel_size=3, padding=1, bias=False),
            nn.GroupNorm(num_groups=g, num_channels=64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2),  # 32x32 -> 16x16
            nn.Conv2d(64, 128, kernel_size=3, padding=1, bias=False),
            nn.GroupNorm(num_groups=g, num_channels=128),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2),  # 16x16 -> 8x8
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * 8 * 8, 256),
            nn.ReLU(inplace=True),
            nn.Dropout(p=dropout),
            nn.Linear(256, num_classes),
        )

        self._init_weights()

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, mean=0.0, std=0.01)
                nn.init.zeros_(m.bias)
            elif isinstance(m, nn.GroupNorm):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.features(x))


def build_model(
    name: str,
    num_classes: int = 10,
    in_channels: int = 3,
    group_norm_groups: int = 2,
) -> nn.Module:
    """Model factory keyed by config['model']['name']."""
    name = name.lower()
    if name == "cnn":
        return GroupNormCNN(
            num_classes=num_classes,
            in_channels=in_channels,
            group_norm_groups=group_norm_groups,
        )
    raise ValueError(f"Unknown model '{name}'. Available: 'cnn'.")
