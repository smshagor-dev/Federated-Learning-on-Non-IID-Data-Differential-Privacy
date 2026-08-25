"""v3 release gate model shared by tests and release tooling."""

from __future__ import annotations

from dataclasses import dataclass

REQUIRED_V3_GATES = (
    "async-runtime",
    "robust-aggregation",
    "privacy-validation",
    "secure-aggregation",
    "algorithm-suite",
    "system-heterogeneity",
    "federated-workloads",
    "distributed-infrastructure",
    "observability",
    "benchmark-matrix",
    "edge-runtime",
    "supply-chain-security",
    "chaos-reliability",
)


@dataclass(frozen=True)
class ReleaseGateReport:
    results: dict[str, bool]

    def missing(self) -> tuple[str, ...]:
        return tuple(
            gate
            for gate in REQUIRED_V3_GATES
            if not self.results.get(gate, False)
        )

    def release_ready(self) -> bool:
        return not self.missing()

    def require_release_ready(self) -> None:
        missing = self.missing()
        if missing:
            raise RuntimeError("v3.0.0 release blocked by: " + ", ".join(missing))


__all__ = ["REQUIRED_V3_GATES", "ReleaseGateReport"]
