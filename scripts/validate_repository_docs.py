from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
RUNTIME_CONTRACT = ROOT / "RUNTIME.md"
CORRECTNESS_CONTRACT = ROOT / "docs" / "runtime-correctness.md"
MAIN = ROOT / "main.py"
COMPOSE = ROOT / "infra" / "compose" / "docker-compose.dev.yml"
README_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
LEGACY_ALL_COMMAND = "python main.py" + " all"
UNSUPPORTED_MATH = re.compile(r"(?<!`)(\\\(|\\\)|\\\[|\\\])")


def strip_fenced_code(text: str) -> str:
    parts: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            parts.append(line)
    return "".join(parts)


def iter_local_links(text: str) -> list[str]:
    links: list[str] = []
    for _label, target in README_LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        links.append(target)
    return links


def validate_runtime_contracts(errors: list[str]) -> None:
    required_files = (
        (RUNTIME_CONTRACT, "RUNTIME.md is required."),
        (CORRECTNESS_CONTRACT, "docs/runtime-correctness.md is required."),
        (MAIN, "main.py is required by the root runtime contract."),
        (
            COMPOSE,
            "infra/compose/docker-compose.dev.yml is required by the distributed runtime contract.",
        ),
    )
    for path, message in required_files:
        if not path.exists():
            errors.append(message)
    if errors:
        return

    runtime_text = RUNTIME_CONTRACT.read_text(encoding="utf-8")
    correctness_text = CORRECTNESS_CONTRACT.read_text(encoding="utf-8")
    main_text = MAIN.read_text(encoding="utf-8")

    required_runtime_fragments = (
        "python main.py",
        "root-simulator",
        "distributed-platform",
        "docker compose -f infra/compose/docker-compose.dev.yml up --build",
        "DP-enabled SCAFFOLD is intentionally fail-closed",
        "python scripts/run_benchmark_matrix.py --dry-run",
    )
    for fragment in required_runtime_fragments:
        if fragment not in runtime_text:
            errors.append(f"RUNTIME.md is missing required text: {fragment!r}")

    required_correctness_fragments = (
        "same neighboring relation",
        "target epsilon",
        "at least five unique seeds",
        "partition_indices.npz",
        "paired sign-flip tests",
    )
    for fragment in required_correctness_fragments:
        if fragment not in correctness_text:
            errors.append(
                "docs/runtime-correctness.md is missing required text: "
                f"{fragment!r}"
            )

    if "from experiment_runtime import" not in main_text:
        errors.append(
            "RUNTIME.md declares the root runtime, but main.py no longer imports "
            "experiment_runtime. Update code and runtime documentation together."
        )


def main() -> int:
    text = README.read_text(encoding="utf-8")
    prose_only = strip_fenced_code(text)
    errors: list[str] = []

    if "python main.py" not in text:
        errors.append("README.md must include `python main.py`.")
    if LEGACY_ALL_COMMAND in text:
        errors.append("README.md must not include the legacy `all` form.")
    if "E:\\" in text or "C:\\" in text:
        errors.append("README.md must not contain local Windows filesystem paths.")
    if UNSUPPORTED_MATH.search(prose_only):
        errors.append(
            "README.md contains unsupported GitHub math delimiters "
            "(use $...$ or $$...$$ outside code fences)."
        )

    for target in iter_local_links(text):
        normalized = target.split("#", 1)[0]
        if not normalized:
            continue
        normalized = unquote(normalized)
        line_match = re.match(r"^(.*?)(:\d+)?$", normalized)
        path_target = line_match.group(1) if line_match else normalized
        candidate = (README.parent / path_target).resolve()
        try:
            candidate.relative_to(ROOT.resolve())
        except ValueError:
            marker = f"{ROOT.name}/"
            if marker in path_target:
                suffix = path_target.split(marker, 1)[1]
                candidate = (ROOT / suffix).resolve()
                try:
                    candidate.relative_to(ROOT.resolve())
                except ValueError:
                    errors.append(f"README link escapes repository root: {target}")
                    continue
            else:
                errors.append(f"README link escapes repository root: {target}")
                continue
        if not candidate.exists():
            errors.append(f"README link target does not exist: {target}")

    validate_runtime_contracts(errors)

    if errors:
        print("Repository documentation validation failed:\n")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Repository documentation validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
