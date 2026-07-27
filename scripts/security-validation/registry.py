"""Security Runtime Completion and Release Evidence slice, Work
Package E: aggregates every group's scenario list into one versioned
registry.

SCHEMA_VERSION bumps whenever a scenario's shape changes in a way that
would break a consumer of summary.json (e.g. a field renamed/removed) --
not on every scenario added/removed, which is expected to happen
routinely as coverage grows.
"""

from __future__ import annotations

from framework import Scenario

SCHEMA_VERSION = 1


def all_scenarios() -> list[Scenario]:
    # Imported lazily (inside the function, not at module import time) so
    # `python run.py --list` can enumerate the registry without requiring
    # every group's own imports (e.g. grpc-adjacent ones) to succeed in
    # an environment that only wants the static scenario list.
    from groups import (  # noqa: PLC0415
        audit_journal,
        event_centralization,
        event_journal,
        metrics,
        privacy_authenticity,
        recovery,
        regression,
        secure_hybrid_dp,
        secure_user_level_dp,
        security_api,
        security_ui,
        signed_messages,
        signed_tasks,
        transport,
        worker_identity,
        worker_keys,
    )

    scenarios: list[Scenario] = []
    for module in (
        transport,
        worker_identity,
        worker_keys,
        signed_messages,
        privacy_authenticity,
        signed_tasks,
        event_centralization,
        event_journal,
        audit_journal,
        security_api,
        security_ui,
        metrics,
        secure_user_level_dp,
        secure_hybrid_dp,
        recovery,
        regression,
    ):
        scenarios.extend(module.SCENARIOS)

    ids = [s.scenario_id for s in scenarios]
    duplicates = {sid for sid in ids if ids.count(sid) > 1}
    if duplicates:
        raise ValueError(f"duplicate scenario_id(s) in the registry: {duplicates}")

    return scenarios


def group_names() -> list[str]:
    seen: list[str] = []
    for scenario in all_scenarios():
        if scenario.category not in seen:
            seen.append(scenario.category)
    return seen
