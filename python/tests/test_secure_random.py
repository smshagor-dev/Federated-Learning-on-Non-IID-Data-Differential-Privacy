"""Tests for fl_platform.privacy.secure_random — the Secure Aggregation
and Cryptographic Protocols category's closure-gate work on truthful
secure-random capability detection. See
docs/secure-aggregation-architecture.md.
"""

from __future__ import annotations

import unittest
from unittest import mock

from fl_platform.privacy import (
    SecureRandomTaskRejectedError,
    SecureRandomUnavailableError,
    require_secure_random,
    secure_random_available,
    worker_reports_secure_random_support,
)
from fl_platform.privacy.secure_random import (
    opacus_secure_mode_available,
    require_opacus_secure_mode,
)
from fl_platform.privacy.secure_random import secure_random_available as _real_available


class SecureRandomAvailableTests(unittest.TestCase):
    def test_is_true_on_a_real_cpython_process(self) -> None:
        # secrets.token_bytes is stdlib and always works on a real
        # CPython interpreter -- this is a real probe, not a mock, so a
        # regression that breaks the underlying call would show up here.
        self.assertTrue(secure_random_available())

    def test_reflects_a_genuinely_broken_entropy_source(self) -> None:
        with mock.patch(
            "fl_platform.privacy.secure_random.secrets.token_bytes",
            side_effect=OSError("entropy source unavailable"),
        ):
            self.assertFalse(_real_available())

    def test_reflects_an_unexpected_return_length(self) -> None:
        with mock.patch(
            "fl_platform.privacy.secure_random.secrets.token_bytes",
            return_value=b"too short",
        ):
            self.assertFalse(_real_available())


class RequireSecureRandomTests(unittest.TestCase):
    def test_does_not_raise_when_available(self) -> None:
        require_secure_random()  # must not raise

    def test_raises_when_unavailable(self) -> None:
        with (
            mock.patch(
                "fl_platform.privacy.secure_random.secure_random_available",
                return_value=False,
            ),
            self.assertRaises(SecureRandomUnavailableError),
        ):
            require_secure_random()


class WorkerCapabilityAdvertisementTests(unittest.TestCase):
    def test_reflects_real_opacus_secure_mode_availability_not_stdlib_csprng(
        self,
    ) -> None:
        # secure_random_available() (stdlib CSPRNG access) is always True
        # and is deliberately irrelevant here -- worker capability
        # advertisement must track whether Opacus's own secure_mode can
        # actually be enabled, which depends on torchcsprng.
        self.assertTrue(secure_random_available())
        self.assertEqual(
            worker_reports_secure_random_support(), opacus_secure_mode_available()
        )

    def test_available_and_unavailable_providers_via_mocking(self) -> None:
        with mock.patch(
            "fl_platform.privacy.secure_random.opacus_secure_mode_available",
            return_value=True,
        ):
            self.assertTrue(worker_reports_secure_random_support())
        with mock.patch(
            "fl_platform.privacy.secure_random.opacus_secure_mode_available",
            return_value=False,
        ):
            self.assertFalse(worker_reports_secure_random_support())


class OpacusSecureModeTests(unittest.TestCase):
    def test_available_matches_a_real_find_spec_probe(self) -> None:
        import importlib.util

        expected = importlib.util.find_spec("torchcsprng") is not None
        self.assertEqual(opacus_secure_mode_available(), expected)

    def test_require_raises_a_structured_error_when_unavailable(self) -> None:
        with mock.patch(
            "fl_platform.privacy.secure_random.opacus_secure_mode_available",
            return_value=False,
        ):
            with self.assertRaises(SecureRandomTaskRejectedError) as ctx:
                require_opacus_secure_mode(client_id="client-a")
            self.assertIn("client-a", str(ctx.exception))
            self.assertIn("torchcsprng", str(ctx.exception))

    def test_require_does_not_raise_when_available(self) -> None:
        with mock.patch(
            "fl_platform.privacy.secure_random.opacus_secure_mode_available",
            return_value=True,
        ):
            require_opacus_secure_mode(client_id="client-a")  # must not raise


if __name__ == "__main__":
    unittest.main()
