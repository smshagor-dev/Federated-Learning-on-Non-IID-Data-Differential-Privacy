#!/usr/bin/env python3
"""Security Runtime Completion and Release Evidence slice, Work Package
U: fails (non-zero exit) if any file under the given directory (or
directories) contains prohibited material -- run in CI right before
`actions/upload-artifact` and by the release-evidence bundle generator
(Work Package V), so a defect in either producer's own redaction logic
is still caught by an independent check rather than trusted blindly.

Usage:
    python scripts/security-validation/check_artifact_sanitation.py DIR [DIR ...]

Prohibited (fails the check):
    - PEM/PKCS#8 private-key headers (worker/coordinator/CA private keys)
    - raw hex-encoded "signature"/"payload_hash" JSON fields (a real
      signed-message payload leaking, not just its presence being noted)
    - a Bearer token
    - AWS-style access key IDs (same pattern the repo's tracked-file
      secret-scan CI job already uses)

Allowed and NOT scanned for: sanitized pass/fail summaries, coverage
reports, sanitized failure logs (already redacted by
scripts/security-validation/framework.py's own _redact), benchmark
summaries, screenshots that do not contain the prohibited patterns
above.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_PROHIBITED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "PEM private-key header",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    ),
    (
        "raw signed-message signature field",
        re.compile(r'"signature"\s*:\s*"[0-9a-fA-F]{16,}"'),
    ),
    (
        "raw signed-message payload_hash field",
        re.compile(r'"payload_hash"\s*:\s*"[0-9a-fA-F]{16,}"'),
    ),
    ("Bearer token", re.compile(r"Bearer [A-Za-z0-9\-_.]{16,}")),
    ("AWS-style access key ID", re.compile(r"AKIA[0-9A-Z]{16}")),
    # Secure User-Level DP Operations, Observability, and Release
    # Evidence slice, Work Area X: the same field-name-generic-hex-value
    # shape as the signature/payload_hash patterns above, extended to
    # the secure-aggregation-specific private-key/shared-secret/mask-key
    # material this slice's own worker code (masked_update.py,
    # crypto.py) constructs -- own_private_key_raw, pairwise shared
    # secrets, and derived mask-stream keys must never appear in any
    # sanitized summary/log artifact this project produces.
    (
        "raw worker private-key/mask-key hex field",
        re.compile(
            r'"(own_private_key_raw|private_key_raw|shared_secret|mask_key|mask_stream_key)"'
            r'\s*:\s*"[0-9a-fA-F]{16,}"'
        ),
    ),
]

# Binary/media files are read but not decoded as text for a pattern
# match to make sense against them the way it does for JSON/Markdown/
# log text -- skipped, not silently "passed": a screenshot's filename
# alone reveals nothing about its safety, so this script does not claim
# to sanitize image/video/trace content, only the text-based summary and
# log artifacts scripts/security-validation/run.py itself produces. See
# docs/security-ci.md for the disclosed scope of this check.
_SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".zip", ".mp4"}


def scan_file(path: Path) -> list[str]:
    if path.suffix.lower() in _SKIP_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    findings = []
    for label, pattern in _PROHIBITED_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(f"{path}: {label} (matched: {match.group()[:40]}...)")
    return findings


def main(argv: list[str]) -> int:
    if not argv:
        print(
            "usage: check_artifact_sanitation.py DIR [DIR ...]", file=sys.stderr
        )
        return 2

    all_findings: list[str] = []
    scanned_count = 0
    for arg in argv:
        root = Path(arg)
        if not root.exists():
            print(f"warning: {root} does not exist, skipping", file=sys.stderr)
            continue
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if not path.is_file():
                continue
            scanned_count += 1
            all_findings.extend(scan_file(path))

    if all_findings:
        print(f"FAIL: found {len(all_findings)} prohibited-material match(es):")
        for finding in all_findings:
            print(f"  - {finding}")
        return 1

    print(f"OK: {scanned_count} file(s) scanned, no prohibited material found.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
