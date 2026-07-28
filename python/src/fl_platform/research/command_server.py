from __future__ import annotations

import json
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .command_auth import (
    CommandAuthenticationError,
    StaticBearerCommandAuthenticator,
)
from .command_contracts import (
    CommandStatus,
    command_result_to_json,
    parse_command_envelope,
    sha256_json,
)
from .command_service import ResearchCommandService

MAX_COMMAND_REQUEST_BYTES = 512 * 1024


class ResearchCommandHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        command_service: ResearchCommandService,
        authenticator: StaticBearerCommandAuthenticator,
    ) -> None:
        super().__init__(server_address, _ResearchCommandHandler)
        self.command_service = command_service
        self.authenticator = authenticator


class _ResearchCommandHandler(BaseHTTPRequestHandler):
    server: ResearchCommandHTTPServer

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/healthz":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            return
        self._write_json(
            HTTPStatus.OK,
            {"service": "python-research-command", "status": "ok"},
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/internal/research/commands":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
            return
        try:
            self.server.authenticator.authenticate(
                self.headers.get("Authorization", ""),
                self.headers.get("X-Service-Identity", ""),
            )
        except CommandAuthenticationError:
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid content length"},
            )
            return
        if content_length <= 0 or content_length > MAX_COMMAND_REQUEST_BYTES:
            self._write_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "request body exceeds bounded internal command size"},
            )
            return
        body = self.rfile.read(content_length)
        try:
            decoded = body.decode("utf-8")
            payload = json.loads(decoded)
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
            hash_payload = json.loads(decoded, parse_float=Decimal, parse_int=Decimal)
            if not isinstance(hash_payload, dict):
                raise ValueError("body must be a JSON object")
            command = parse_command_envelope(payload)
        except (json.JSONDecodeError, ValueError) as error:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        result = self.server.command_service.execute(
            command,
            payload_hash_override=sha256_json(hash_payload.get("payload", {})),
        )
        status = _status_code_for_result(result.status)
        self._write_json(status, command_result_to_json(result))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _status_code_for_result(status: CommandStatus) -> HTTPStatus:
    return {
        CommandStatus.SUCCEEDED: HTTPStatus.OK,
        CommandStatus.VALIDATION_FAILED: HTTPStatus.BAD_REQUEST,
        CommandStatus.CONFLICT: HTTPStatus.CONFLICT,
        CommandStatus.NOT_FOUND: HTTPStatus.NOT_FOUND,
        CommandStatus.PERMISSION_CONTEXT_REJECTED: HTTPStatus.FORBIDDEN,
        CommandStatus.EXPIRED: HTTPStatus.GONE,
        CommandStatus.CANCELED: HTTPStatus.CONFLICT,
        CommandStatus.STORAGE_DEGRADED: HTTPStatus.SERVICE_UNAVAILABLE,
        CommandStatus.CORRUPTION_DETECTED: HTTPStatus.CONFLICT,
        CommandStatus.UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
        CommandStatus.INTERNAL_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,
    }[status]
