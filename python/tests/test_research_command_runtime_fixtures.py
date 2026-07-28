from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from fl_platform.research.command_contracts import sha256_json


class ResearchCommandRuntimeFixtureTests(unittest.TestCase):
    def test_fixed_runtime_fixtures_match_python_verification_payload(self) -> None:
        fixtures = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "testdata"
                / "research_command_runtime_fixtures.json"
            ).read_text(encoding="utf-8")
        )["fixtures"]

        for fixture in fixtures:
            with self.subTest(fixture=fixture["name"]):
                normalized = json.loads(
                    fixture["expected_payload_canonical_json"],
                    parse_float=Decimal,
                    parse_int=Decimal,
                )
                canonical = self._canonical(normalized)
                self.assertEqual(
                    canonical,
                    fixture["expected_payload_canonical_json"],
                )
                self.assertEqual(
                    len(canonical.encode("utf-8")),
                    fixture["expected_payload_byte_length"],
                )
                self.assertEqual(
                    sha256_json(normalized),
                    fixture["expected_payload_sha256"],
                )

    @staticmethod
    def _canonical(payload: object) -> str:
        if payload is None:
            return "null"
        if payload is True:
            return "true"
        if payload is False:
            return "false"
        if isinstance(payload, str):
            return json.dumps(payload, ensure_ascii=True)
        if isinstance(payload, Decimal):
            return format(payload, "f")
        if isinstance(payload, list):
            return (
                "["
                + ",".join(
                    ResearchCommandRuntimeFixtureTests._canonical(item)
                    for item in payload
                )
                + "]"
            )
        if isinstance(payload, dict):
            return (
                "{"
                + ",".join(
                    f"{json.dumps(str(key), ensure_ascii=True)}:"
                    f"{ResearchCommandRuntimeFixtureTests._canonical(payload[key])}"
                    for key in sorted(payload)
                )
                + "}"
            )
        raise AssertionError(f"unexpected canonical type: {type(payload)!r}")
