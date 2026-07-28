from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_python_path() -> None:
    repo_root = Path(__file__).resolve().parent
    python_src = repo_root / "python" / "src"
    if str(python_src) not in sys.path:
        sys.path.insert(0, str(python_src))


def main() -> int:
    _bootstrap_python_path()
    from fl_platform.cli.application import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
