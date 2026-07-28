from __future__ import annotations

import secrets
from dataclasses import dataclass


class CommandAuthenticationError(PermissionError):
    """Raised when the internal command caller is not authenticated."""


@dataclass(slots=True)
class StaticBearerCommandAuthenticator:
    expected_bearer_secret: str
    expected_service_identity: str

    def authenticate(self, authorization_header: str, service_identity: str) -> None:
        expected_header = f"Bearer {self.expected_bearer_secret}"
        if not secrets.compare_digest(authorization_header, expected_header):
            raise CommandAuthenticationError("invalid internal command bearer secret")
        if not secrets.compare_digest(
            service_identity.strip(), self.expected_service_identity
        ):
            raise CommandAuthenticationError("invalid internal caller identity")
