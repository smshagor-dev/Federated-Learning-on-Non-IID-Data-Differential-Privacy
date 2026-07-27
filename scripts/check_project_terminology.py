"""Repository terminology validation gate.

Enforces the project-wide naming policy: engineering categories are named
after what they actually implement (Foundation, Aggregation Core,
Coordinator Runtime, Algorithm Expansion, Privacy Engineering, ...), not
numbered roadmap labels. Fails if "milestone"/"milestones" (any case) or
a standalone "M<N>" alias (M1..M10, capital M only) appears in any
repository-owned file.

Design notes on avoiding false positives (see the two regexes below):
* "milestone"/"milestones" is matched case-insensitively — there is no
  legitimate identifier in this codebase that collides with that word.
* "M<N>" is matched case-SENSITIVELY (capital M only) and only as a
  whole token (`\\b...\\b`). This deliberately does NOT match lowercase
  moment-accumulator variables like `m1`/`m2` in FedAdam/FedYogi code
  (see tests/baseline/test_cpp_golden_parity.py), nor identifiers where
  a digit-bearing token is glued to more identifier characters by an
  underscore (`m12_tensor`, `model_mlp` never match `\\bM[0-9]...\\b`
  because `_` is a word character, so there is no boundary there).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Exactly the paths the naming policy requires this checker to scan.
SCAN_TARGETS = [
    "README.md",
    "docs",
    "cpp",
    "python",
    "go",
    "web",
    "proto",
    "infra",
    "scripts",
    "tests",
    ".github",
    "Makefile",
    "docker-compose.yml",
]

# Directory names excluded anywhere they appear under a scan target.
EXCLUDED_DIR_NAMES = {
    ".git",
    "build",
    "node_modules",
    ".next",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
    ".coverage",
    "htmlcov",
    "generated",  # regenerated on demand from proto/ — see docs/protobuf-generation.md
    "vendor",
    "dist",
    ".tox",
}

# Specific files/globs excluded even inside an otherwise-scanned directory.
EXCLUDED_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".csv",  # benchmark result artifacts
    ".pb",  # protobuf descriptor sets
    ".lock",
}

EXCLUDED_FILE_NAMES = {
    "package-lock.json",
    "go.sum",
}

# fl_platform.egg-info is package-manager-generated metadata (regenerated
# by `pip install -e`), not hand-authored repository content. This
# checker's own source is excluded for the obvious reason: a script that
# documents and matches the prohibited terms must necessarily name them
# in its docstring, comments, and pattern definitions.
EXCLUDED_PATH_SUBSTRINGS = (
    "fl_platform.egg-info",
    ".tsbuildinfo",
    "scripts/check_project_terminology.py",
    "scripts\\check_project_terminology.py",
)

MILESTONE_WORD_PATTERN = re.compile(r"\bmilestones?\b", re.IGNORECASE)
NUMBERED_ALIAS_PATTERN = re.compile(r"\bM(10|[1-9])\b")


def is_excluded(path: Path) -> bool:
    parts = path.parts
    if any(part in EXCLUDED_DIR_NAMES for part in parts):
        return True
    if path.name in EXCLUDED_FILE_NAMES:
        return True
    if path.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
        return True
    path_str = str(path)
    return any(marker in path_str for marker in EXCLUDED_PATH_SUBSTRINGS)


def iter_scan_files() -> list[Path]:
    files: list[Path] = []
    for target in SCAN_TARGETS:
        target_path = ROOT / target
        if not target_path.exists():
            continue
        if target_path.is_file():
            if not is_excluded(target_path.relative_to(ROOT)):
                files.append(target_path)
            continue
        for candidate in target_path.rglob("*"):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(ROOT)
            if is_excluded(relative):
                continue
            files.append(candidate)
    return files


def scan_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []  # binary or unreadable file — not repository-authored prose/code text
    findings = []
    relative = path.relative_to(ROOT)
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern, label in (
            (MILESTONE_WORD_PATTERN, "milestone"),
            (NUMBERED_ALIAS_PATTERN, "numbered alias"),
        ):
            match = pattern.search(line)
            if match:
                findings.append(
                    f"{relative}:{line_number}: prohibited {label} term "
                    f"{match.group(0)!r}: {line.strip()}"
                )
    return findings


def main() -> int:
    all_findings: list[str] = []
    for path in iter_scan_files():
        all_findings.extend(scan_file(path))

    if all_findings:
        print("Prohibited roadmap terminology found:\n")
        for finding in sorted(all_findings):
            print(finding)
        print(
            f"\n{len(all_findings)} violation(s). Use category-based "
            "terminology (Foundation, Aggregation Core, Coordinator "
            "Runtime, Algorithm Expansion, Privacy Engineering, Secure "
            "Aggregation and Cryptographic Protocols, Distributed "
            "Execution, Enterprise Platform, Observability and "
            "Operations, Production Hardening) instead."
        )
        return 1

    print("terminology check passed: no prohibited roadmap terminology found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
