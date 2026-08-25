from __future__ import annotations

import pytest

from fl_platform.privacy.ledger import SampleLevelLedgerEntry
from fl_platform.v3.privacy_validation import (
    PrivacyUtilityPoint,
    PrivacyValidationReport,
    build_privacy_utility_curve,
    gradient_leakage_similarity,
    membership_inference_auc,
    validate_sample_ledger_resume,
)


def _entry(round_id: int, epsilon: float, entry_id: str) -> SampleLevelLedgerEntry:
    return SampleLevelLedgerEntry(
        run_id="run-privacy",
        round_id=round_id,
        client_id="client-1",
        epsilon=epsilon,
        delta=1e-5,
        noise_multiplier=1.2,
        sample_rate=0.1,
        steps=10 * (round_id + 1),
        accountant="rdp",
        entry_id=entry_id,
    )


def test_membership_inference_auc_detects_separable_attack_signal() -> None:
    result = membership_inference_auc(
        member_scores=(0.95, 0.90, 0.85, 0.80),
        nonmember_scores=(0.40, 0.35, 0.30, 0.25),
    )
    assert result.auc == pytest.approx(1.0)
    assert result.advantage == pytest.approx(1.0)


def test_membership_inference_auc_ties_are_half_credit() -> None:
    result = membership_inference_auc(
        member_scores=(0.5, 0.5),
        nonmember_scores=(0.5, 0.5),
    )
    assert result.auc == pytest.approx(0.5)
    assert result.advantage == pytest.approx(0.0)


def test_membership_inference_supports_loss_orientation() -> None:
    result = membership_inference_auc(
        member_scores=(0.1, 0.2, 0.15),
        nonmember_scores=(0.8, 0.9, 0.7),
        higher_means_member=False,
    )
    assert result.auc == pytest.approx(1.0)


def test_gradient_leakage_similarity_reports_reconstruction_quality() -> None:
    exact = gradient_leakage_similarity((1.0, -2.0, 3.0), (1.0, -2.0, 3.0))
    assert exact.cosine_similarity == pytest.approx(1.0)
    assert exact.normalized_l2_error == pytest.approx(0.0)

    weak = gradient_leakage_similarity((1.0, 0.0), (0.0, 1.0))
    assert weak.cosine_similarity == pytest.approx(0.0)
    assert weak.normalized_l2_error == pytest.approx(2**0.5)


def test_ledger_resume_preserves_prefix_and_monotonic_epsilon() -> None:
    before = (_entry(0, 0.5, "e0"), _entry(1, 0.8, "e1"))
    after = (*before, _entry(2, 1.1, "e2"))
    result = validate_sample_ledger_resume(before, after)
    assert result.valid
    assert result.entries_before_restart == 2
    assert result.entries_after_resume == 3
    assert result.latest_epsilon == pytest.approx(1.1)


def test_ledger_resume_rejects_prefix_tampering_and_epsilon_regression() -> None:
    before = (_entry(0, 0.5, "e0"), _entry(1, 0.8, "e1"))
    tampered = (_entry(0, 0.6, "e0"), _entry(1, 0.8, "e1"))
    assert not validate_sample_ledger_resume(before, tampered).valid

    regressed = (*before, _entry(2, 0.7, "e2"))
    result = validate_sample_ledger_resume(before, regressed)
    assert not result.valid
    assert result.reason == "epsilon regressed after resume"


def test_ledger_resume_rejects_duplicate_entry_identity() -> None:
    before = (_entry(0, 0.5, "e0"),)
    duplicated = (*before, _entry(1, 0.8, "e0"))
    result = validate_sample_ledger_resume(before, duplicated)
    assert not result.valid
    assert result.reason == "duplicate privacy ledger entry_id"


def test_privacy_utility_curve_is_ordered_and_machine_readable() -> None:
    curve = build_privacy_utility_curve(
        (
            PrivacyUtilityPoint(4.0, 1e-5, 0.91, 0.58),
            PrivacyUtilityPoint(1.0, 1e-5, 0.84, 0.52),
            PrivacyUtilityPoint(2.0, 1e-5, 0.88, 0.55),
        )
    )
    assert tuple(point.epsilon for point in curve) == (1.0, 2.0, 4.0)
    report = PrivacyValidationReport(
        membership_inference=membership_inference_auc(
            (0.51, 0.49, 0.50),
            (0.50, 0.48, 0.52),
        ),
        gradient_leakage=gradient_leakage_similarity((1.0, 0.0), (0.2, 0.9)),
        utility_curve=curve,
        ledger_resume=validate_sample_ledger_resume(
            (_entry(0, 0.5, "e0"),),
            (_entry(0, 0.5, "e0"), _entry(1, 0.8, "e1")),
        ),
    )
    record = report.to_record()
    assert record["ledger_resume"]["valid"] is True
    assert len(record["utility_curve"]) == 3


def test_privacy_report_fails_closed_on_invalid_resume_evidence() -> None:
    before = (_entry(0, 0.5, "e0"),)
    invalid = validate_sample_ledger_resume(before, ())
    report = PrivacyValidationReport(ledger_resume=invalid)
    with pytest.raises(ValueError, match="resume validation failed"):
        report.to_record()
