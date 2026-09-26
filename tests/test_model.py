"""Acceptance tests for controlproof.model — task B1."""

from __future__ import annotations

import inspect
import json

import pytest

from controlproof.model import (
    AUTHORITIES,
    CONFIDENT_RESULTS,
    CONTROL_ACTION_NOT_OBSERVED,
    CONTROL_CONFIGURED_NOT_EXECUTED,
    CONTROL_CONFLICTING_EVIDENCE,
    CONTROL_ENFORCEMENT_VERIFIED,
    CONTROL_NOT_CONFIGURED,
    CONTROL_NOT_DETERMINED,
    CONTROL_NOT_ENFORCEABLE,
    CONTROL_OBSERVATIONAL_ONLY,
    EVIDENCE_NOT_VERIFIED,
    NO_EVIDENCE,
    RESULT_LABELS,
    RESULT_TONE,
    Conflict,
    ControlFacts,
    NOT_DETERMINED,
    authority_for,
    blocking_capable,
    classify,
    derive_enforceable,
    derive_states,
    evaluate,
    state_symbol,
    state_to_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_facts(**kwargs) -> ControlFacts:
    """Build ControlFacts with bundle_verified=True and bob_version='2.2.0' as defaults."""
    defaults = {
        "bundle_verified": True,
        "bob_version": "2.2.0",
    }
    defaults.update(kwargs)
    return ControlFacts(**defaults)


# Demo arms -------------------------------------------------------------------

def pre_arm() -> ControlFacts:
    return make_facts(
        control_id="CP-001-PRE",
        configured=True,
        configured_event="PreToolUse",
        actual_event="PreToolUse",
        ledger_rows=2,
        ledger_corroborated=True,
        target_in_payload=True,
        policy_triggered=True,
        bob_cancelled_citing_control=True,
        target_exists_after=False,
    )


def post_arm() -> ControlFacts:
    return make_facts(
        control_id="CP-001-POST",
        configured=True,
        configured_event="PostToolUse",
        actual_event="PostToolUse",
        ledger_rows=2,
        ledger_corroborated=True,
        target_in_payload=True,
        policy_triggered=True,
        bob_cancelled_citing_control=False,
        target_exists_after=True,
    )


def badcfg_arm() -> ControlFacts:
    return make_facts(
        control_id="CP-001-BADCFG",
        configured=True,
        configured_event="PreToolUse",
        ledger_rows=0,
        absence_witnessed=True,
        target_exists_after=True,
    )


# ---------------------------------------------------------------------------
# 1. test_lifecycle_authority_tables_exact
# ---------------------------------------------------------------------------


def test_lifecycle_authority_tables_exact():
    assert set(AUTHORITIES.keys()) == {"2.1.0", "2.2.0"}

    expected_210 = {
        "SessionStart": False,
        "UserPromptSubmit": True,
        "PreToolUse": True,
        "PostToolUse": False,
        "Stop": False,
    }
    expected_220 = {
        "SessionStart": False,
        "UserPromptSubmit": True,
        "PreToolUse": True,
        "PostToolUse": False,
        "PostCompact": False,
        "Stop": False,
    }

    assert dict(AUTHORITIES["2.1.0"].blocking) == expected_210
    assert dict(AUTHORITIES["2.2.0"].blocking) == expected_220


# ---------------------------------------------------------------------------
# 2. test_blocking_capable_unknown_is_not_determined
# ---------------------------------------------------------------------------


def test_blocking_capable_unknown_is_not_determined():
    authority_220 = AUTHORITIES["2.2.0"]

    assert authority_220.blocking_capable(None) is NOT_DETERMINED
    assert authority_220.blocking_capable("Notification") is NOT_DETERMINED
    assert authority_220.blocking_capable("PreCompact") is NOT_DETERMINED

    # any event with authority=None
    for event in ("PreToolUse", "PostToolUse", "SessionStart", None, "Unknown"):
        assert blocking_capable(event, None) is NOT_DETERMINED


# ---------------------------------------------------------------------------
# 3. test_pre_arm_enforcement_verified
# ---------------------------------------------------------------------------


def test_pre_arm_enforcement_verified():
    facts = pre_arm()
    states = derive_states(facts)

    assert states["configured"] is True
    assert states["executed"] is True
    assert states["observed"] is True
    assert states["enforceable"] is True
    assert states["enforced"] is True

    rec = evaluate(facts)
    assert rec.result == CONTROL_ENFORCEMENT_VERIFIED


# ---------------------------------------------------------------------------
# 4. test_post_arm_observational_only
# ---------------------------------------------------------------------------


def test_post_arm_observational_only():
    facts = post_arm()
    states = derive_states(facts)

    assert states["configured"] is True
    assert states["executed"] is True
    assert states["observed"] is True
    assert states["enforceable"] is False
    assert states["enforced"] is False

    rec = evaluate(facts)
    assert rec.result == CONTROL_OBSERVATIONAL_ONLY


# ---------------------------------------------------------------------------
# 5. test_badcfg_arm_configured_not_executed
# ---------------------------------------------------------------------------


def test_badcfg_arm_configured_not_executed():
    facts = badcfg_arm()
    states = derive_states(facts)

    assert states["configured"] is True
    assert states["executed"] is False
    assert states["observed"] is False
    assert states["enforceable"] is NOT_DETERMINED
    assert states["enforced"] is False

    rec = evaluate(facts)
    assert rec.result == CONTROL_CONFIGURED_NOT_EXECUTED


# ---------------------------------------------------------------------------
# 6. test_enforceable_independent_of_outcome
# ---------------------------------------------------------------------------


def test_enforceable_independent_of_outcome():
    tri_values = [True, False, NOT_DETERMINED]

    for version in ("2.1.0", "2.2.0"):
        authority = AUTHORITIES[version]
        events = list(authority.blocking.keys()) + [None, "UnknownEvent"]

        for event in events:
            expected_enforceable = blocking_capable(event, authority)

            for pt in tri_values:
                for bcc in tri_values:
                    for tea in tri_values:
                        facts = make_facts(
                            control_id="TEST",
                            bob_version=version,
                            configured=True,
                            configured_event=event,
                            actual_event=event,
                            ledger_rows=2,
                            ledger_corroborated=True,
                            target_in_payload=True,
                            policy_triggered=pt,
                            bob_cancelled_citing_control=bcc,
                            target_exists_after=tea,
                        )
                        states = derive_states(facts)
                        assert states["enforceable"] is expected_enforceable, (
                            f"version={version} event={event} pt={pt} bcc={bcc} tea={tea}: "
                            f"expected enforceable={expected_enforceable!r}, "
                            f"got {states['enforceable']!r}"
                        )


# ---------------------------------------------------------------------------
# 7. test_derive_enforceable_signature
# ---------------------------------------------------------------------------


def test_derive_enforceable_signature():
    sig = inspect.signature(derive_enforceable)
    params = list(sig.parameters.keys())
    assert params == ["executed", "actual_event", "authority"]


# ---------------------------------------------------------------------------
# 7b. test_unknown_bob_version_is_not_determined
# ---------------------------------------------------------------------------


def test_unknown_bob_version_is_not_determined():
    for version in ("9.9.9", None):
        facts = make_facts(
            control_id="CP-001-PRE",
            bob_version=version,
            configured=True,
            configured_event="PreToolUse",
            actual_event="PreToolUse",
            ledger_rows=2,
            ledger_corroborated=True,
            target_in_payload=True,
            policy_triggered=True,
            bob_cancelled_citing_control=True,
            target_exists_after=False,
        )
        states = derive_states(facts)
        assert states["enforceable"] is NOT_DETERMINED, f"version={version}"

        rec = evaluate(facts)
        assert rec.result == CONTROL_NOT_DETERMINED, f"version={version}"
        assert rec.lifecycle_authority_source is None, f"version={version}"


# ---------------------------------------------------------------------------
# 8. test_enforceable_is_not_enforced
# ---------------------------------------------------------------------------


def test_enforceable_is_not_enforced():
    # PRE arm but target_exists_after=True => enforced becomes False
    facts = make_facts(
        control_id="CP-001-PRE",
        configured=True,
        configured_event="PreToolUse",
        actual_event="PreToolUse",
        ledger_rows=2,
        ledger_corroborated=True,
        target_in_payload=True,
        policy_triggered=True,
        bob_cancelled_citing_control=True,
        target_exists_after=True,  # overridden
    )
    states = derive_states(facts)
    assert states["enforceable"] is True
    assert states["enforced"] is False

    rec = evaluate(facts)
    assert rec.result != CONTROL_ENFORCEMENT_VERIFIED


# ---------------------------------------------------------------------------
# 9. test_ledger_alone_is_not_execution
# ---------------------------------------------------------------------------


def test_ledger_alone_is_not_execution():
    for corroborated in (NOT_DETERMINED, False):
        facts = make_facts(
            control_id="TEST",
            configured=True,
            ledger_rows=3,
            ledger_corroborated=corroborated,
        )
        states = derive_states(facts)
        assert states["executed"] is NOT_DETERMINED, f"corroborated={corroborated}"

        rec = evaluate(facts)
        assert rec.result == CONTROL_NOT_DETERMINED, f"corroborated={corroborated}"


# ---------------------------------------------------------------------------
# 10. test_missing_ledger_without_witness_is_not_determined
# ---------------------------------------------------------------------------


def test_missing_ledger_without_witness_is_not_determined():
    facts = make_facts(
        control_id="CP-001-BADCFG",
        configured=True,
        configured_event="PreToolUse",
        ledger_rows=0,
        absence_witnessed=False,  # no witness
        target_exists_after=True,
    )
    states = derive_states(facts)
    assert states["executed"] is NOT_DETERMINED

    rec = evaluate(facts)
    assert rec.result == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 11. test_conflict_blocks_confident_result
# ---------------------------------------------------------------------------


def test_conflict_blocks_confident_result():
    conflict = Conflict(
        code="CF-001",
        artifacts=("artifact_a", "artifact_b"),
        detail="Artifacts disagree on execution time.",
    )
    facts = dataclasses.replace(pre_arm(), conflicts=(conflict,))
    rec = evaluate(facts)
    assert rec.result == CONTROL_CONFLICTING_EVIDENCE
    assert rec.confident is False


# ---------------------------------------------------------------------------
# 12. test_unverified_bundle_suppresses_result
# ---------------------------------------------------------------------------


def test_unverified_bundle_suppresses_result():
    conflict = Conflict(
        code="CF-001",
        artifacts=("artifact_a",),
        detail="Some conflict.",
    )
    facts = dataclasses.replace(pre_arm(), conflicts=(conflict,), bundle_verified=False)
    rec = evaluate(facts)
    assert rec.result == EVIDENCE_NOT_VERIFIED


# ---------------------------------------------------------------------------
# 13. test_bundle_verified_defaults_false
# ---------------------------------------------------------------------------


def test_bundle_verified_defaults_false():
    rec = evaluate(ControlFacts(control_id="X", configured=True))
    assert rec.result == EVIDENCE_NOT_VERIFIED


# ---------------------------------------------------------------------------
# 14. test_not_configured and test_no_evidence
# ---------------------------------------------------------------------------


def test_not_configured():
    facts = make_facts(control_id="X", configured=False)
    rec = evaluate(facts)
    assert rec.result == CONTROL_NOT_CONFIGURED


def test_no_evidence():
    # all defaults with bundle_verified=True → no evidence
    facts = make_facts(control_id="X")
    rec = evaluate(facts)
    assert rec.result == NO_EVIDENCE


# ---------------------------------------------------------------------------
# 15. test_not_determined_is_never_truthy_or_false
# ---------------------------------------------------------------------------


def test_not_determined_is_never_truthy_or_false():
    with pytest.raises(TypeError):
        bool(NOT_DETERMINED)

    assert NOT_DETERMINED is not False
    assert state_symbol(NOT_DETERMINED) == "\u2014"


# ---------------------------------------------------------------------------
# 16. test_uncertainty_results_not_confident
# ---------------------------------------------------------------------------


def test_uncertainty_results_not_confident():
    uncertain = {
        CONTROL_CONFLICTING_EVIDENCE,
        CONTROL_NOT_DETERMINED,
        EVIDENCE_NOT_VERIFIED,
        NO_EVIDENCE,
    }
    for code in uncertain:
        assert code not in CONFIDENT_RESULTS, f"{code} should not be in CONFIDENT_RESULTS"


# ---------------------------------------------------------------------------
# 17. test_record_to_dict_json
# ---------------------------------------------------------------------------


def test_record_to_dict_json():
    # Use unknown bob_version to ensure a NOT_DETERMINED state appears
    facts = make_facts(
        control_id="CP-001-PRE",
        bob_version="9.9.9",
        configured=True,
        configured_event="PreToolUse",
        actual_event="PreToolUse",
        ledger_rows=2,
        ledger_corroborated=True,
        target_in_payload=True,
        policy_triggered=True,
        bob_cancelled_citing_control=True,
        target_exists_after=False,
    )
    rec = evaluate(facts)
    d = rec.to_dict()
    # Must be JSON-serializable
    serialized = json.dumps(d)
    assert serialized  # non-empty

    # NOT_DETERMINED states must serialize as "not_determined"
    states = d["states"]
    assert states["enforceable"] == "not_determined"


# ---------------------------------------------------------------------------
# 18. test_wording
# ---------------------------------------------------------------------------


def test_wording():
    import re

    banned = re.compile(
        r"proven|secure|guaranteed?|tamper[- ]?proof|certified",
        re.IGNORECASE,
    )

    # Check all result labels
    for label in RESULT_LABELS.values():
        assert not banned.search(label), f"Banned word in label: {label!r}"

    # Check authority sources
    for auth in AUTHORITIES.values():
        assert not banned.search(auth.source), f"Banned word in source: {auth.source!r}"

    # Check reasons generated for every demo arm
    for facts in (pre_arm(), post_arm(), badcfg_arm()):
        rec = evaluate(facts)
        assert not banned.search(rec.reason), f"Banned word in reason: {rec.reason!r}"

    # Also check a few other result paths
    extra_facts = [
        make_facts(control_id="X", configured=False),
        make_facts(control_id="X"),
        make_facts(control_id="X", configured=True, configured_event="PreToolUse",
                   actual_event="PreToolUse", ledger_rows=2, ledger_corroborated=True,
                   target_in_payload=True, policy_triggered=True,
                   bob_cancelled_citing_control=True, target_exists_after=False,
                   bob_version="9.9.9"),
    ]
    for facts in extra_facts:
        rec = evaluate(facts)
        assert not banned.search(rec.reason), f"Banned word in reason: {rec.reason!r}"


# ---------------------------------------------------------------------------
# Need dataclasses for replace
# ---------------------------------------------------------------------------
import dataclasses  # noqa: E402  (used in tests above via dataclasses.replace)
