"""Research command-service entrypoint (`python -m fl_platform.research`).

This launches the bounded internal Python command service that owns all
durable research-registry mutations. It is intentionally private to the
control-plane network: Go is the public API, Python is the authoritative
writer.
"""

from __future__ import annotations

import os

from .command_auth import StaticBearerCommandAuthenticator
from .command_server import ResearchCommandHTTPServer
from .command_service import ResearchCommandService


def main() -> None:
    root = os.environ.get("FL_RESEARCH_ROOT", "/var/control-plane/research")
    host = os.environ.get("FL_RESEARCH_COMMAND_HOST", "0.0.0.0")
    port = int(os.environ.get("FL_RESEARCH_COMMAND_PORT", "8090"))
    bearer_secret = os.environ.get("FL_RESEARCH_COMMAND_SECRET", "")
    service_identity = os.environ.get(
        "FL_RESEARCH_COMMAND_SERVICE_IDENTITY", "go-control-plane"
    )
    if not bearer_secret:
        raise SystemExit("FL_RESEARCH_COMMAND_SECRET is required")
    server = ResearchCommandHTTPServer(
        (host, port),
        ResearchCommandService(root),
        StaticBearerCommandAuthenticator(
            expected_bearer_secret=bearer_secret,
            expected_service_identity=service_identity,
        ),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
