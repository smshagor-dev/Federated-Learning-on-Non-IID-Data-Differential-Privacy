#!/usr/bin/env python
"""Inspects development-PKI certificates issued by generate-dev-ca.sh /
issue-service-cert.sh / issue-worker-cert.sh — see
docs/development-pki.md. Prints exactly the fields the Go security API
(Work Package O) and web Transport Security / Worker Identity panels
(Work Package P) are meant to expose: subject, URI SAN identity, serial,
fingerprint, validity window, and expiry status — never the private key,
which this script never even opens.

Usage:
    python scripts/pki/inspect-certificates.py <path-to-cert.pem> [more-certs...]
    python scripts/pki/inspect-certificates.py --dir certs/dev
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import ExtensionOID, NameOID


@dataclass(slots=True, frozen=True)
class CertificateSummary:
    """Exactly the safe fields this tool ever surfaces -- deliberately
    the same shape the Go security API's safe-field-filtering
    requirement (Work Package O) targets, so this tool doubles as a
    reference for what "safe certificate metadata" means for this
    project."""

    path: str
    subject_common_name: str
    uri_san: str | None
    serial_number: str
    sha256_fingerprint: str
    not_valid_before: str
    not_valid_after: str
    is_expired: bool
    days_until_expiry: int


def summarize_certificate(path: Path) -> CertificateSummary:
    pem_bytes = path.read_bytes()
    cert = x509.load_pem_x509_certificate(pem_bytes)

    common_names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    common_name = str(common_names[0].value) if common_names else "(no CN)"

    uri_san: str | None = None
    try:
        san_ext = cert.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )
        uris = san_ext.value.get_values_for_type(x509.UniformResourceIdentifier)
        uri_san = uris[0] if uris else None
    except x509.ExtensionNotFound:
        uri_san = None

    fingerprint = cert.fingerprint(hashes.SHA256()).hex(":").upper()
    now = datetime.now(UTC)
    not_after = cert.not_valid_after_utc
    not_before = cert.not_valid_before_utc
    days_until_expiry = (not_after - now).days

    return CertificateSummary(
        path=str(path),
        subject_common_name=common_name,
        uri_san=uri_san,
        serial_number=format(cert.serial_number, "x"),
        sha256_fingerprint=fingerprint,
        not_valid_before=not_before.isoformat(),
        not_valid_after=not_after.isoformat(),
        is_expired=now > not_after,
        days_until_expiry=days_until_expiry,
    )


def _print_summary(summary: CertificateSummary) -> None:
    print(f"--- {summary.path} ---")
    print(f"  subject CN:        {summary.subject_common_name}")
    print(f"  URI SAN identity:  {summary.uri_san or '(none)'}")
    print(f"  serial:            {summary.serial_number}")
    print(f"  sha256 fingerprint:{summary.sha256_fingerprint}")
    print(f"  valid from:        {summary.not_valid_before}")
    print(f"  valid until:       {summary.not_valid_after}")
    if summary.is_expired:
        print("  status:            EXPIRED")
    elif summary.days_until_expiry <= 14:
        print(f"  status:            expiring soon ({summary.days_until_expiry} days)")
    else:
        print(f"  status:            valid ({summary.days_until_expiry} days remaining)")


def _find_certs(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.cert.pem"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("certs", nargs="*", help="Certificate PEM file paths")
    parser.add_argument(
        "--dir", type=Path, default=None, help="Directory to search for *.cert.pem"
    )
    args = parser.parse_args(argv)

    paths: list[Path] = [Path(p) for p in args.certs]
    if args.dir is not None:
        paths.extend(_find_certs(args.dir))

    if not paths:
        parser.print_usage()
        return 1

    exit_code = 0
    for path in paths:
        if not path.exists():
            print(f"--- {path} ---\n  ERROR: file not found", file=sys.stderr)
            exit_code = 1
            continue
        try:
            summary = summarize_certificate(path)
        except (ValueError, OSError) as error:
            print(f"--- {path} ---\n  ERROR: {error}", file=sys.stderr)
            exit_code = 1
            continue
        _print_summary(summary)
        if summary.is_expired:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
