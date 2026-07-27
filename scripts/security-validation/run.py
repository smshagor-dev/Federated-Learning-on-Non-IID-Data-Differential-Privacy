#!/usr/bin/env python3
"""Security Runtime Completion and Release Evidence slice, Work Package
D: CLI entrypoint for the modular security runtime-validation harness.

Usage:
    python scripts/security-validation/run.py --list
    python scripts/security-validation/run.py --group transport,event-centralization
    python scripts/security-validation/run.py --scenario transport.mtls.status.enforced
    python scripts/security-validation/run.py  # every group

Brings up infra/compose/docker-compose.dev.yml +
infra/compose/docker-compose.security.yml (postgres, redis, coordinator,
api, python-worker -- plus web when the security-ui group is selected),
runs every selected, runnable scenario in registry order, tears the
stack down, and writes both a machine-readable JSON summary and a
human-readable Markdown summary. Exits non-zero if any REQUIRED
scenario ends FAIL.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from framework import (  # noqa: E402
    REPO_ROOT,
    Context,
    RunSummary,
    Status,
    eprint,
    now_iso,
    run_scenario,
    write_reports,
)
from registry import all_scenarios  # noqa: E402

COMPOSE_FILES = (
    "infra/compose/docker-compose.dev.yml",
    "infra/compose/docker-compose.security.yml",
)
API_BASE = "http://localhost:8080"
CORE_SERVICES = ("postgres", "redis", "coordinator", "api", "python-worker")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--group", type=str, default="", help="comma-separated group names (default: all)"
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default="",
        help="comma-separated exact scenario_id values (default: all in selected groups)",
    )
    parser.add_argument("--list", action="store_true", help="print the registry and exit")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="artifacts/security-runtime-validation",
        help="where to write summary.json/summary.md (repo-relative)",
    )
    parser.add_argument(
        "--no-compose", action="store_true", help="assume the stack is already running"
    )
    parser.add_argument(
        "--keep-stack", action="store_true", help="do not tear down the stack afterward"
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def select_scenarios(args: argparse.Namespace) -> list:
    scenarios = all_scenarios()
    if args.group:
        groups = {g.strip() for g in args.group.split(",") if g.strip()}
        scenarios = [s for s in scenarios if s.category in groups]
    if args.scenario:
        ids = {s.strip() for s in args.scenario.split(",") if s.strip()}
        scenarios = [s for s in scenarios if s.scenario_id in ids]
    return scenarios


def print_registry(scenarios: list) -> None:
    by_category: dict[str, list] = {}
    for scenario in scenarios:
        by_category.setdefault(scenario.category, []).append(scenario)
    for category in sorted(by_category):
        print(f"\n[{category}]")
        for scenario in by_category[category]:
            marker = "RUN" if scenario.run is not None else scenario.support_status.value
            required = "required" if scenario.required else "optional"
            print(f"  {marker:9s} {required:9s} {scenario.scenario_id} -- {scenario.name}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scenarios = select_scenarios(args)

    if args.list:
        print_registry(scenarios)
        return 0

    if not scenarios:
        eprint("no scenarios matched the given --group/--scenario filters")
        return 1

    with tempfile.TemporaryDirectory(prefix="fl-security-validation-") as workspace_str:
        workspace = Path(workspace_str)
        ctx = Context(
            api_base=API_BASE,
            coordinator_address="localhost:50051",
            compose_files=COMPOSE_FILES,
            workspace=workspace,
            verbose=args.verbose,
        )

        started_up = False
        try:
            if not args.no_compose:
                print(f"Bringing up: {', '.join(CORE_SERVICES)} (real mTLS override)")
                ctx.compose("up", "-d", *CORE_SERVICES, timeout=180.0)
                started_up = True
                print("Waiting for the Go API to become reachable...")
                healthy = ctx.wait_for_health(
                    f"{API_BASE}/api/v1/security/overview", timeout_seconds=15.0
                ) or ctx.wait_for_health(f"{API_BASE}/", timeout_seconds=60.0)
                if not healthy:
                    eprint(
                        "warning: could not confirm API health within the wait window; "
                        "proceeding anyway (individual scenarios will surface real failures)"
                    )

            results = []
            for scenario in scenarios:
                label = f"{scenario.scenario_id}"
                print(f"-> {label} ... ", end="", flush=True)
                result = run_scenario(scenario, ctx)
                print(result.status.value)
                if result.status == Status.FAIL and args.verbose:
                    print(f"     {result.detail}")
                results.append(result)

        finally:
            if started_up and not args.keep_stack:
                print("Tearing down...")
                try:
                    ctx.compose("down", "-v", check=False, timeout=120.0)
                except Exception as error:  # noqa: BLE001 - teardown must not mask the real result
                    eprint(f"warning: teardown failed: {error}")

        summary = RunSummary(started_at=now_iso(), finished_at=now_iso())
        summary.results = results
        output_dir = REPO_ROOT / args.output_dir
        json_path, md_path = write_reports(summary, output_dir)

        counts = summary.counts()
        print(
            f"\n{counts['PASS']} PASS, {counts['FAIL']} FAIL, {counts['BLOCKED']} BLOCKED, "
            f"{counts['DEFERRED']} DEFERRED, {counts['SKIPPED']} SKIPPED"
        )
        print(f"JSON summary: {json_path}")
        print(f"Markdown summary: {md_path}")

        required_failures = summary.required_failed()
        if required_failures:
            eprint(f"\n{len(required_failures)} REQUIRED scenario(s) failed:")
            for result in required_failures:
                eprint(f"  - {result.scenario.scenario_id}: {result.detail}")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
