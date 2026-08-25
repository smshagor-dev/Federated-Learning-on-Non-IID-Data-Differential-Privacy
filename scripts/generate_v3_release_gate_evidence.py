#!/usr/bin/env python3
"""Generate machine-readable v3.0.0 gate qualification evidence."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PYTHON_SRC = _REPO_ROOT / "python" / "src"
sys.path.insert(0, str(_PYTHON_SRC))

from fl_platform.v3.release_gates import REQUIRED_V3_GATES  # noqa: E402
from fl_platform.v3.release_support import (  # noqa: E402
    GATE_QUALIFICATIONS,
    release_support_payload,
    validate_release_support_contract,
)

EXTERNAL_SAME_SHA_WORKFLOWS = (
    "ci.yml",
    "v3-release-candidate.yml",
    "v3-distributed-runtime.yml",
    "v3-final-qualification.yml",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-evidence", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def _project_version() -> str:
    payload = tomllib.loads(
        (_REPO_ROOT / "python" / "pyproject.toml").read_text(encoding="utf-8")
    )
    return str(payload["project"]["version"])


def main() -> int:
    args = parse_args()
    commit_sha = str(args.commit_sha).strip().lower()
    benchmark_path = Path(args.benchmark_evidence).resolve()
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))

    validate_release_support_contract()
    if _project_version() != "3.0.0":
        raise ValueError("final qualification requires package version 3.0.0")
    if benchmark.get("release") != "3.0.0":
        raise ValueError("benchmark evidence release version mismatch")
    if benchmark.get("evidence_complete") is not True:
        raise ValueError("benchmark evidence is incomplete")
    if str(benchmark.get("commit_sha", "")).lower() != commit_sha:
        raise ValueError("benchmark evidence commit mismatch")

    support = release_support_payload()
    gates = []
    for gate in REQUIRED_V3_GATES:
        qualification = GATE_QUALIFICATIONS[gate]
        gates.append(
            {
                "gate": gate,
                "status": "qualified",
                "mode": qualification.mode.value,
                "stable_capabilities": list(qualification.stable_capabilities),
                "experimental_exclusions": list(
                    qualification.experimental_exclusions
                ),
                "checks": list(qualification.checks),
            }
        )

    evidence = {
        "schema_version": 1,
        "release": "3.0.0",
        "commit_sha": commit_sha,
        "qualification_complete": True,
        "gate_count": len(gates),
        "gates": gates,
        "support_contract": support,
        "benchmark_evidence": {
            "path": benchmark_path.name,
            "evidence_complete": True,
            "plan_hash": benchmark.get("plan_hash"),
            "observations_sha256": benchmark.get("observations_sha256"),
        },
        "external_same_sha_workflows_required": list(EXTERNAL_SAME_SHA_WORKFLOWS),
        "release_ready_rule": (
            "The v3.0.0 tag workflow must verify successful same-SHA runs for every "
            "required workflow before publishing release artifacts."
        ),
    }

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
