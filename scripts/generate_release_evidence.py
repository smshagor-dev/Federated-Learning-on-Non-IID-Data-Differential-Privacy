#!/usr/bin/env python3
"""Security Runtime Completion and Release Evidence slice, Work Package
V: assembles a reproducible, sanitized evidence bundle at
artifacts/security-release-evidence/ summarizing the current state of
every security-relevant regression gate and the live runtime-validation
harness -- for a release reviewer to read, not for re-deriving anything
programmatically.

This script does not itself bring up Docker or run the runtime harness
(that is scripts/security-validation/run.py's job, and it can take
several minutes) -- it assembles the bundle from build/test commands it
runs directly (terminology, protobuf contracts, pytest/go test/npm test
pass-fail counts) plus whatever the runtime harness already wrote to
--output-dir (default artifacts/security-runtime-validation/), copied
in as-is since that output is already sanitized by
scripts/security-validation/framework.py's own _redact(). Run the
runtime harness first (or pass --skip-runtime-harness if you only want
the static-check evidence, e.g. for a quick local check).

Usage:
    python scripts/security-validation/run.py \
        --output-dir artifacts/security-runtime-validation
    python scripts/generate_release_evidence.py

Every file this script writes is text (JSON/Markdown/plain summaries)
-- never a full test log, never raw command output beyond a bounded
tail, matching docs/security-ci.md's artifact-sanitation policy. The
final step re-scans the whole assembled bundle with
scripts/security-validation/check_artifact_sanitation.py and fails
loudly (non-zero exit, bundle left on disk for inspection) if anything
prohibited is found -- this script's own text-writing must never be
trusted blindly either.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_TAIL_CHARS = 4000


@dataclass(slots=True)
class CheckResult:
    name: str
    command: list[str]
    returncode: int
    output_tail: str


def _run(
    name: str, command: list[str], *, cwd: Path = REPO_ROOT, timeout: float = 600.0
) -> CheckResult:
    print(f"-> {name}: {' '.join(command)}")
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )
    combined = (result.stdout + result.stderr).strip()
    tail = combined[-_TAIL_CHARS:]
    print(f"   exit={result.returncode}")
    return CheckResult(
        name=name, command=command, returncode=result.returncode, output_tail=tail
    )


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _git_dirty() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return bool(result.stdout.strip())


def _write_check_report(output_dir: Path, check: CheckResult) -> None:
    path = output_dir / f"{check.name}.txt"
    path.write_text(
        f"command: {' '.join(check.command)}\n"
        f"exit_code: {check.returncode}\n"
        f"status: {'PASS' if check.returncode == 0 else 'FAIL'}\n"
        f"--- output (tail, {_TAIL_CHARS} chars max) ---\n"
        f"{check.output_tail}\n",
        encoding="utf-8",
    )


def _copy_runtime_harness_output(source_dir: Path, dest_dir: Path) -> bool:
    summary_json = source_dir / "summary.json"
    summary_md = source_dir / "summary.md"
    if not summary_json.exists() or not summary_md.exists():
        return False
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(summary_json, dest_dir / "summary.json")
    shutil.copy2(summary_md, dest_dir / "summary.md")
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-harness-dir",
        default="artifacts/security-runtime-validation",
        help="where scripts/security-validation/run.py already wrote its summary "
        "(repo-relative)",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/security-release-evidence",
        help="where to write the assembled evidence bundle (repo-relative)",
    )
    parser.add_argument(
        "--skip-runtime-harness",
        action="store_true",
        help="do not require/copy a runtime-harness summary (static checks only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = REPO_ROOT / args.output_dir
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    checks = [
        _run("terminology-check", ["python", "scripts/check_project_terminology.py"]),
        _run(
            "protobuf-contract-check", ["python", "scripts/verify_proto_contracts.py"]
        ),
        _run("python-tests", ["python", "-m", "pytest", "tests", "python/tests", "-q"]),
        _run("python-ruff-lint", ["python", "-m", "ruff", "check", "."]),
        _run(
            "go-tests",
            ["go", "test", "./..."],
            cwd=REPO_ROOT / "go",
        ),
        _run(
            "go-vet",
            ["go", "vet", "./..."],
            cwd=REPO_ROOT / "go",
        ),
    ]
    for check in checks:
        _write_check_report(output_dir, check)

    runtime_copied = False
    if not args.skip_runtime_harness:
        runtime_copied = _copy_runtime_harness_output(
            REPO_ROOT / args.runtime_harness_dir,
            output_dir / "security-runtime-validation",
        )
        if not runtime_copied:
            print(
                f"warning: no summary.json/summary.md found under "
                f"{args.runtime_harness_dir} -- run "
                "`python scripts/security-validation/run.py` first for full "
                "evidence. Continuing with static-check evidence only.",
                file=sys.stderr,
            )

    manifest = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": _git_commit(),
        "git_working_tree_dirty": _git_dirty(),
        "checks": [
            {"name": c.name, "status": "PASS" if c.returncode == 0 else "FAIL"}
            for c in checks
        ],
        "runtime_harness_evidence_included": runtime_copied,
        "scope_note": (
            "This bundle documents Secure Aggregation and Cryptographic Protocols "
            "engineering-category security-observability work (transport, worker/"
            "coordinator signing keys, message and privacy-record authenticity, "
            "event/audit journals). It does NOT claim secure aggregation, pairwise "
            "masking, secret sharing, dropout recovery, homomorphic encryption, "
            "Byzantine-robust aggregation, or remote attestation are implemented -- "
            "see docs/known-limitations.md and docs/security-events.md's "
            "feature_availability contract for the live, queryable version of this "
            "same disclosure."
        ),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    sanitation = subprocess.run(
        [
            sys.executable,
            "scripts/security-validation/check_artifact_sanitation.py",
            str(output_dir),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    print(sanitation.stdout)
    if sanitation.returncode != 0:
        print(sanitation.stderr, file=sys.stderr)
        print(
            "FAIL: the assembled evidence bundle contains prohibited material -- "
            "left on disk at",
            output_dir,
            "for inspection; do not distribute it.",
            file=sys.stderr,
        )
        return 1

    failed_checks = [c for c in checks if c.returncode != 0]
    print(f"\nEvidence bundle written to {output_dir}")
    if failed_checks:
        failed_names = [c.name for c in failed_checks]
        print(f"warning: {len(failed_checks)} check(s) failed: {failed_names}")
        print(
            "The bundle still reflects the real, current state -- "
            "failures are recorded, not hidden."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
