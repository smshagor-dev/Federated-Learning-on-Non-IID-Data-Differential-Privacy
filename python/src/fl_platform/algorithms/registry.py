"""Algorithm-name -> FederatedLocalAlgorithm lookup, used by
task_runner.py/service.py instead of a growing if/elif chain."""

from __future__ import annotations

from fl_platform.algorithms.base import FederatedLocalAlgorithm

_REGISTRY: dict[str, FederatedLocalAlgorithm] = {}


def register_algorithm(name: str, algorithm: FederatedLocalAlgorithm) -> None:
    _REGISTRY[name] = algorithm


def get_algorithm(name: str) -> FederatedLocalAlgorithm:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"unknown algorithm '{name}'; registered: {sorted(_REGISTRY)}"
        ) from None


def registered_algorithm_names() -> list[str]:
    return sorted(_REGISTRY)


def _install_defaults() -> None:
    # Imported lazily (not at module import time) to avoid a circular
    # import: each algorithm module imports FederatedLocalAlgorithm from
    # base.py, and legacy_adapter.py imports task_runner.py, which does
    # not itself depend on the registry.
    from fl_platform.algorithms.ditto import DittoAlgorithm
    from fl_platform.algorithms.fedsam import FedSamAlgorithm
    from fl_platform.algorithms.legacy_adapter import LegacyAlgorithmAdapter
    from fl_platform.algorithms.per_fedavg import PerFedAvgAlgorithm

    register_algorithm("fedavg", LegacyAlgorithmAdapter("fedavg"))
    register_algorithm("fedprox", LegacyAlgorithmAdapter("fedprox"))
    register_algorithm("scaffold", LegacyAlgorithmAdapter("scaffold"))
    register_algorithm("fedsam", FedSamAlgorithm())
    register_algorithm("ditto", DittoAlgorithm())
    register_algorithm("per_fedavg", PerFedAvgAlgorithm())


_install_defaults()
