from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from fl_platform.v3.async_checkpoint import AsyncStateStore, AsyncStateStoreError
from fl_platform.v3.async_execution import (
    AsyncExecutionError,
    DurableAsyncResultProcessor,
)
from fl_platform.v3.async_membership import ElasticClientRegistry
from fl_platform.v3.async_runtime import (
    AsyncModelState,
    AsyncStateSnapshot,
    AsyncUpdate,
)
from fl_platform.workers import TrainingResult


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _result(
    client_id: str,
    *,
    base_version: int | None,
    update: tuple[float, ...] | None,
    accepted: bool = True,
) -> TrainingResult:
    return TrainingResult(
        run_id="run-1",
        round_id=4,
        client_id=client_id,
        model_version=f"model-v{base_version if base_version is not None else 0}",
        sample_count=10,
        local_step_count=2,
        accepted=accepted,
        worker_id="worker-a",
        trace_id="trace-1",
        base_model_version=base_version,
        model_update=update,
    )


def test_async_snapshot_round_trip_preserves_replay_fence() -> None:
    state = AsyncModelState((0.0, 0.0), mixing_alpha=0.5, max_staleness=3)
    update = AsyncUpdate(
        "client-a",
        0,
        (2.0, -2.0),
        update_id="update-a",
        payload_digest=_digest("payload-a"),
    )
    assert state.apply(update).accepted

    restored = AsyncModelState.from_snapshot(state.snapshot())
    assert restored.model == pytest.approx((1.0, -1.0))
    assert restored.version == 1
    duplicate = restored.apply(update)
    assert not duplicate.accepted
    assert duplicate.reason == "duplicate update"

    conflict = restored.apply(
        AsyncUpdate(
            "client-a",
            0,
            (3.0, -3.0),
            update_id="update-a",
            payload_digest=_digest("different-payload"),
        )
    )
    assert not conflict.accepted
    assert conflict.reason == "conflicting replay"


def test_async_checkpoint_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    state = AsyncModelState((1.0, 2.0), mixing_alpha=0.25, max_staleness=2)
    store = AsyncStateStore(tmp_path / "async-state.json")
    store.save(state.snapshot())
    assert store.load() == state.snapshot()

    envelope = json.loads(store.path.read_text(encoding="utf-8"))
    envelope["payload_sha256"] = "0" * 64
    store.path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(AsyncStateStoreError, match="checksum"):
        store.load()


def test_durable_processor_rejects_replay_after_restart(tmp_path: Path) -> None:
    store = AsyncStateStore(tmp_path / "async-state.json")
    processor = DurableAsyncResultProcessor.load_or_create(
        store,
        (0.0, 0.0),
        mixing_alpha=0.5,
        max_staleness=4,
    )
    first_result = _result("client-a", base_version=0, update=(2.0, -2.0))
    first = processor.apply_result(first_result)
    assert first.apply_result.accepted
    assert processor.state.version == 1
    assert processor.state.model == pytest.approx((1.0, -1.0))

    restarted = DurableAsyncResultProcessor.load_or_create(
        store,
        (0.0, 0.0),
        mixing_alpha=0.5,
        max_staleness=4,
    )
    assert restarted.state.version == 1
    duplicate = restarted.apply_result(first_result)
    assert not duplicate.apply_result.accepted
    assert duplicate.apply_result.reason == "duplicate update"
    assert restarted.state.version == 1

    changed = _result("client-a", base_version=0, update=(4.0, -4.0))
    conflict = restarted.apply_result(changed)
    assert not conflict.apply_result.accepted
    assert conflict.apply_result.reason == "conflicting replay"
    assert restarted.state.version == 1


def test_durable_processor_applies_staleness_per_result(tmp_path: Path) -> None:
    processor = DurableAsyncResultProcessor.load_or_create(
        AsyncStateStore(tmp_path / "async-state.json"),
        (0.0,),
        mixing_alpha=1.0,
        max_staleness=1,
    )
    first = processor.apply_result(_result("a", base_version=0, update=(2.0,)))
    second = processor.apply_result(_result("b", base_version=0, update=(2.0,)))
    too_stale = processor.apply_result(_result("c", base_version=0, update=(2.0,)))

    assert first.apply_result.accepted
    assert first.apply_result.staleness == 0
    assert second.apply_result.accepted
    assert second.apply_result.staleness == 1
    assert second.apply_result.weight == pytest.approx(0.5)
    assert not too_stale.apply_result.accepted
    assert too_stale.apply_result.reason == "too stale"
    assert processor.state.model == pytest.approx((3.0,))
    assert processor.state.version == 2


def test_durable_processor_requires_explicit_base_version(tmp_path: Path) -> None:
    processor = DurableAsyncResultProcessor.load_or_create(
        AsyncStateStore(tmp_path / "async-state.json"),
        (0.0,),
    )
    outcome = processor.apply_result(
        _result("client-a", base_version=None, update=(1.0,))
    )
    assert not outcome.apply_result.accepted
    assert "base_model_version" in (outcome.apply_result.reason or "")
    assert processor.state.version == 0


def test_durable_processor_rolls_back_if_checkpoint_commit_fails(
    tmp_path: Path,
) -> None:
    class FailingStore(AsyncStateStore):
        def save(self, snapshot: AsyncStateSnapshot) -> None:
            del snapshot
            raise AsyncStateStoreError("forced failure")

    store = FailingStore(tmp_path / "async-state.json")
    processor = DurableAsyncResultProcessor(
        AsyncModelState((0.0,), mixing_alpha=0.5),
        store,
    )
    with pytest.raises(AsyncExecutionError, match="rolled back"):
        processor.apply_result(_result("client-a", base_version=0, update=(2.0,)))
    assert processor.state.version == 0
    assert processor.state.model == pytest.approx((0.0,))


def test_elastic_membership_expiry_rejoin_and_generation_fencing() -> None:
    registry = ElasticClientRegistry()
    first = registry.join("client-a", now=10.0, lease_seconds=5.0)
    assert first.generation == 1
    renewed = registry.heartbeat(
        "client-a",
        first.generation,
        now=12.0,
        lease_seconds=5.0,
    )
    assert renewed.expires_at == pytest.approx(17.0)
    assert registry.accepts("client-a", 1, now=16.0)
    assert not registry.accepts("client-a", 1, now=17.0)

    second = registry.join("client-a", now=18.0, lease_seconds=5.0)
    assert second.generation == 2
    with pytest.raises(ValueError, match="stale client generation"):
        registry.heartbeat("client-a", 1, now=19.0, lease_seconds=5.0)

    restored = ElasticClientRegistry.from_snapshot(registry.snapshot())
    active = restored.active_clients(now=20.0)
    assert active == (second,)
    assert restored.accepts("client-a", 2, now=20.0)
