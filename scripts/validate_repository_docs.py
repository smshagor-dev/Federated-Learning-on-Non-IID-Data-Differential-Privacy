from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PROHIBITED_PHRASE = re.compile(
    r"research" + r"[ -]" + r"projects?",
    re.IGNORECASE,
)
README_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
LEGACY_ALL_COMMAND = "python main.py" + " all"


def iter_local_links(text: str) -> list[str]:
    links: list[str] = []
    for _label, target in README_LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        links.append(target)
    return links


def main() -> int:
    text = README.read_text(encoding="utf-8")
    errors: list[str] = []

    if "python main.py" not in text:
        errors.append("README.md must include `python main.py`.")
    if LEGACY_ALL_COMMAND in text:
        errors.append("README.md must not include the legacy `all` form.")
    if PROHIBITED_PHRASE.search(text):
        errors.append("README.md still contains the prohibited repository phrase.")
    if "E:\\" in text or "C:\\" in text:
        errors.append("README.md must not contain local Windows filesystem paths.")

    for target in iter_local_links(text):
        normalized = target.split("#", 1)[0]
        if not normalized:
            continue
        candidate = (README.parent / normalized).resolve()
        try:
            candidate.relative_to(ROOT.resolve())
        except ValueError:
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
