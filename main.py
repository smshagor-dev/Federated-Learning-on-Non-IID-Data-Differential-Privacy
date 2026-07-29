"""Desktop-first entry point for the federated learning research studio."""

from __future__ import annotations

import sys

from fl_platform.cli import application


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        return application.main()
    return application.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
