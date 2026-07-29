from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PROHIBITED_PHRASE = re.compile(
    r"research" + r"[ -]" + r"projects?",
    re.IGNORECASE,
)
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


def main() -> int:
    text = README.read_text(encoding="utf-8")
    prose_only = strip_fenced_code(text)
    errors: list[str] = []

    if "python main.py" not in text:
        errors.append("README.md must include `python main.py`.")
    if LEGACY_ALL_COMMAND in text:
        errors.append("README.md must not include the legacy `all` form.")
    if PROHIBITED_PHRASE.search(text):
        errors.append("README.md still contains the prohibited repository phrase.")
    if "E:\\" in text or "C:\\" in text:
        errors.append("README.md must not contain local Windows filesystem paths.")
    if UNSUPPORTED_MATH.search(prose_only):
        errors.append(
            "README.md contains unsupported GitHub math delimiters (use $...$ or $$...$$ outside code fences)."
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

    if errors:
        print("README validation failed:\n")
        for error in errors:
            print(f"- {error}")
        return 1

    print("README validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
