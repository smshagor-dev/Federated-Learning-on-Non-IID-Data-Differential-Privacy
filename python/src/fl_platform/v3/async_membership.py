"""Lease-based elastic client membership for v3 asynchronous execution."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClientLease:
    client_id: str
    generation: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class ElasticMembershipSnapshot:
    schema_version: int
    generations: tuple[tuple[str, int], ...]
    leases: tuple[ClientLease, ...]

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported elastic membership snapshot schema")
        generation_map: dict[str, int] = {}
        for client_id, generation in self.generations:
            if not client_id or generation < 1 or client_id in generation_map:
                raise ValueError("invalid elastic membership generation state")
            generation_map[client_id] = generation
        seen_leases: set[str] = set()
        for lease in self.leases:
            if (
                not lease.client_id
                or lease.client_id in seen_leases
                or lease.generation < 1
                or not math.isfinite(lease.expires_at)
            ):
                raise ValueError("invalid elastic membership lease")
            if generation_map.get(lease.client_id) != lease.generation:
                raise ValueError("lease generation does not match generation state")
            seen_leases.add(lease.client_id)


class ElasticClientRegistry:
    """Track joins, heartbeats, expiry and rejoin generations without barriers."""

    def __init__(self) -> None:
        self._generations: dict[str, int] = {}
        self._leases: dict[str, ClientLease] = {}

    def join(self, client_id: str, *, now: float, lease_seconds: float) -> ClientLease:
        self._validate_time(now, lease_seconds)
        if not client_id:
            raise ValueError("client_id must not be empty")
        self._expire(now)
        if client_id in self._leases:
            raise ValueError("client is already active; use heartbeat to renew its lease")
        generation = self._generations.get(client_id, 0) + 1
        lease = ClientLease(client_id, generation, now + lease_seconds)
        self._generations[client_id] = generation
        self._leases[client_id] = lease
        return lease

    def heartbeat(
        self,
        client_id: str,
        generation: int,
        *,
        now: float,
        lease_seconds: float,
    ) -> ClientLease:
        self._validate_time(now, lease_seconds)
        self._expire(now)
        lease = self._leases.get(client_id)
        if lease is None:
            raise ValueError("client lease is not active")
        if generation != lease.generation:
            raise ValueError("stale client generation")
        renewed = ClientLease(client_id, generation, now + lease_seconds)
        self._leases[client_id] = renewed
        return renewed

    def leave(self, client_id: str, generation: int, *, now: float) -> None:
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        self._expire(now)
        lease = self._leases.get(client_id)
        if lease is None:
            return
        if generation != lease.generation:
            raise ValueError("stale client generation")
        del self._leases[client_id]

    def active_clients(self, *, now: float) -> tuple[ClientLease, ...]:
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        self._expire(now)
        return tuple(self._leases[key] for key in sorted(self._leases))

    def accepts(self, client_id: str, generation: int, *, now: float) -> bool:
        if not math.isfinite(now):
            return False
        self._expire(now)
        lease = self._leases.get(client_id)
        return lease is not None and lease.generation == generation

    def snapshot(self) -> ElasticMembershipSnapshot:
        snapshot = ElasticMembershipSnapshot(
            schema_version=1,
            generations=tuple(sorted(self._generations.items())),
            leases=tuple(self._leases[key] for key in sorted(self._leases)),
        )
        snapshot.validate()
        return snapshot

    @classmethod
    def from_snapshot(
        cls,
        snapshot: ElasticMembershipSnapshot,
    ) -> ElasticClientRegistry:
        snapshot.validate()
        registry = cls()
        registry._generations = dict(snapshot.generations)
        registry._leases = {lease.client_id: lease for lease in snapshot.leases}
        return registry

    @staticmethod
    def _validate_time(now: float, lease_seconds: float) -> None:
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        if lease_seconds <= 0.0 or not math.isfinite(lease_seconds):
            raise ValueError("lease_seconds must be finite and positive")

    def _expire(self, now: float) -> None:
        expired = [
            client_id
            for client_id, lease in self._leases.items()
            if now >= lease.expires_at
        ]
        for client_id in expired:
            del self._leases[client_id]


__all__ = [
    "ClientLease",
    "ElasticClientRegistry",
    "ElasticMembershipSnapshot",
]
