"""Algorithm-name -> FederatedLocalAlgorithm lookup.

Name discovery is intentionally lightweight so benchmark planning and release
contract validation do not need to import the Torch-backed training stack.
Actual algorithm implementations are instantiated only when ``get_algorithm``
needs one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fl_platform.algorithms.base import FederatedLocalAlgorithm

_DEFAULT_ALGORITHM_NAMES = (
    "ditto",
    "fedavg",
    "fedprox",
    "fedsam",
    "per_fedavg",
    "scaffold",
)
_REGISTRY: dict[str, FederatedLocalAlgorithm] = {}
_DEFAULTS_INSTALLED = False


def register_algorithm(name: str, algorithm: FederatedLocalAlgorithm) -> None:
    _REGISTRY[name] = algorithm


def get_algorithm(name: str) -> FederatedLocalAlgorithm:
    if name not in _REGISTRY:
        _install_defaults()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"unknown algorithm '{name}'; registered: {registered_algorithm_names()}"
        ) from None


def registered_algorithm_names() -> list[str]:
    """Return canonical worker algorithm names without loading Torch."""
    return sorted(set(_DEFAULT_ALGORITHM_NAMES).union(_REGISTRY))


def _install_defaults() -> None:
    global _DEFAULTS_INSTALLED
    if _DEFAULTS_INSTALLED:
        return

    # Heavy training dependencies are deliberately imported only when an
    # implementation object is actually requested. ``setdefault`` preserves
    # any caller-provided override registered before the defaults are loaded.
    from fl_platform.algorithms.ditto import DittoAlgorithm
    from fl_platform.algorithms.fedsam import FedSamAlgorithm
    from fl_platform.algorithms.legacy_adapter import LegacyAlgorithmAdapter
    from fl_platform.algorithms.per_fedavg import PerFedAvgAlgorithm

    defaults: dict[str, FederatedLocalAlgorithm] = {
        "fedavg": LegacyAlgorithmAdapter("fedavg"),
        "fedprox": LegacyAlgorithmAdapter("fedprox"),
        "scaffold": LegacyAlgorithmAdapter("scaffold"),
        "fedsam": FedSamAlgorithm(),
        "ditto": DittoAlgorithm(),
        "per_fedavg": PerFedAvgAlgorithm(),
    }
    for name, algorithm in defaults.items():
        _REGISTRY.setdefault(name, algorithm)
    _DEFAULTS_INSTALLED = True
