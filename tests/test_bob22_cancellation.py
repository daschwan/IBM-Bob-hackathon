"""Acceptance tests for B5 — IBM Bob 2.2.0 stored cancellation form.

Tests/fixtures/run-3arm-bob22/ contains the synthetic three-arm run in the
Bob 2.2.0 stored form (every tool message has toolUsage.signature; the blocked
call has isError: true and the hook reason as its whole content).

Tests copy fixtures to pytest's tmp_path before changing them.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from controlproof.conflicts import assess
from controlproof.ingest import (
    BobToolCall,
    ControlEvidence,
    cancellation_for_call,
    parse_cancellation,
)
from controlproof.model import (
    NOT_DETERMINED,
    CONTROL_CONFIGURED_NOT_EXECUTED,
    CONTROL_CONFLICTING_EVIDENCE,
    CONTROL_ENFORCEMENT_VERIFIED,
    CONTROL_NOT_DETERMINED,
    CONTROL_OBSERVATIONAL_ONLY,
    evaluate,
)

FIXTURE_21 = Path(__file__).parent / "fixtures" / "run-3arm"
FIXTURE_22 = Path(__file__).parent / "fixtures" / "run-3arm-bob22"

# Session / tool-use ids from fixtures
SID_PRE = "3f9a1c7e5b2d4f60a8e1c3b5d7f90a12"
# The bob_task filename for PRE arm
PRE_TASK_FILE = f"{SID_PRE}.json"
# The blocked message id
BLOCKED_MSG_ID = "3f9a1c7emsg05"


def copy_fixture(fixture: Path, tmp_path: Path) -> Path:
    """Copy a fixture directory tree into tmp_path and return the copy root."""
    dst = tmp_path / fixture.name
    shutil.copytree(str(fixture), str(dst))
    return dst


def result(ev: ControlEvidence) -> str:
    """evaluate(replace(ev.facts, bundle_verified=True)).result — the result code string."""
    return evaluate(dataclasses.replace(ev.facts, bundle_verified=True)).result


def get_ev(evs: list[ControlEvidence], control_id: str) -> ControlEvidence:
    for ev in evs:
        if ev.facts.control_id == control_id:
            return ev
    raise KeyError(control_id)


def read_pre_task(run_dir: Path) -> dict:
    p = run_dir / "bob_tasks" / PRE_TASK_FILE
    return json.loads(p.read_text(encoding="utf-8"))


def write_pre_task(run_dir: Path, obj: dict) -> None:
    p = run_dir / "bob_tasks" / PRE_TASK_FILE
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def patch_blocked_msg(run_dir: Path, msg_patch: dict) -> None:
    """Apply msg_patch over the blocked message's data dict and save the task."""
    task = read_pre_task(run_dir)
    for msg in task["messages"]:
        if msg.get("id") == BLOCKED_MSG_ID:
            msg["data"].update(msg_patch)
            break
    write_pre_task(run_dir, task)


def patch_signature(run_dir: Path, sig_patch: dict) -> None:
    """Apply sig_patch over the blocked message's toolUsage.signature and save."""
    task = read_pre_task(run_dir)
    for msg in task["messages"]:
        if msg.get("id") == BLOCKED_MSG_ID:
            msg["data"]["toolUsage"]["signature"].update(sig_patch)
            break
    write_pre_task(run_dir, task)


def remove_tool_usage(run_dir: Path) -> None:
    """Remove toolUsage from the blocked message and save."""
    task = read_pre_task(run_dir)
    for msg in task["messages"]:
        if msg.get("id") == BLOCKED_MSG_ID:
            msg["data"].pop("toolUsage", None)
            break
    write_pre_task(run_dir, task)


# ---------------------------------------------------------------------------
# 1. test_bob21_form_still_attributed
# ---------------------------------------------------------------------------


def test_bob21_form_still_attributed(tmp_path):
    """2.1.0 form: PRE → ENFORCEMENT_VERIFIED, POST → OBSERVATIONAL_ONLY,
    BADCFG → CONFIGURED_NOT_EXECUTED."""
    run_dir = copy_fixture(FIXTURE_21, tmp_path)
    evs = assess(run_dir)

    pre = get_ev(evs, "CP-001-PRE")
    post = get_ev(evs, "CP-002-POST")
    badcfg = get_ev(evs, "CP-004-BADCFG")

    assert result(pre) == CONTROL_ENFORCEMENT_VERIFIED
    assert result(post) == CONTROL_OBSERVATIONAL_ONLY
    assert result(badcfg) == CONTROL_CONFIGURED_NOT_EXECUTED


# ---------------------------------------------------------------------------
# 2. test_bob22_form_three_arms
# ---------------------------------------------------------------------------


def test_bob22_form_three_arms(tmp_path):
    """2.2.0 form: PRE bob_cancelled True + ENFORCEMENT_VERIFIED, POST OBSERVATIONAL_ONLY,
    BADCFG CONFIGURED_NOT_EXECUTED, no conflicts."""
    run_dir = copy_fixture(FIXTURE_22, tmp_path)
    evs = assess(run_dir)

    pre = get_ev(evs, "CP-001-PRE")
    post = get_ev(evs, "CP-002-POST")
    badcfg = get_ev(evs, "CP-004-BADCFG")

    assert pre.facts.bob_cancelled_citing_control is True
    assert result(pre) == CONTROL_ENFORCEMENT_VERIFIED
    assert result(post) == CONTROL_OBSERVATIONAL_ONLY
    assert result(badcfg) == CONTROL_CONFIGURED_NOT_EXECUTED
    # No conflicts anywhere
    for ev in evs:
        assert ev.facts.conflicts == ()


# ---------------------------------------------------------------------------
# 3. test_is_error_false_not_attributed
# ---------------------------------------------------------------------------


def test_is_error_false_not_attributed(tmp_path):
    """isError false → not attributed, PRE result NOT_DETERMINED."""
    run_dir = copy_fixture(FIXTURE_22, tmp_path)
    patch_signature(run_dir, {"isError": False})
    evs = assess(run_dir)

    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.bob_cancelled_citing_control is not True
    assert result(pre) == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 4. test_is_error_string_true_not_attributed
# ---------------------------------------------------------------------------


def test_is_error_string_true_not_attributed(tmp_path):
    """isError 'true' and separately 1 are not attributed."""
    for bad_value in ("true", 1):
        run_dir = copy_fixture(FIXTURE_22, tmp_path / str(bad_value))
        patch_signature(run_dir, {"isError": bad_value})
        evs = assess(run_dir)
        pre = get_ev(evs, "CP-001-PRE")
        assert pre.facts.bob_cancelled_citing_control is not True, (
            f"Expected not-True bob_cancelled for isError={bad_value!r}"
        )


# ---------------------------------------------------------------------------
# 5. test_wrong_control_id
# ---------------------------------------------------------------------------


def test_wrong_control_id(tmp_path):
    """Content cites CP-OTHER and separately CP-001-PRE-X: not attributed,
    PRE not ENFORCEMENT_VERIFIED."""
    for bad_id in ("CP-OTHER", "CP-001-PRE-X"):
        run_dir = copy_fixture(FIXTURE_22, tmp_path / bad_id)
        new_content = (
            f"ControlProof {bad_id} denied this write: deny writes under protected/. "
            "nonce=5e1f0c2d9a7b3e41 event=PreToolUse"
        )
        patch_blocked_msg(run_dir, {"content": new_content})
        evs = assess(run_dir)
        pre = get_ev(evs, "CP-001-PRE")
        assert pre.facts.bob_cancelled_citing_control is not True, (
            f"Expected not-True bob_cancelled for control_id={bad_id!r}"
        )
        assert result(pre) != CONTROL_ENFORCEMENT_VERIFIED, (
            f"Should not be ENFORCEMENT_VERIFIED for control_id={bad_id!r}"
        )


# ---------------------------------------------------------------------------
# 6. test_wrong_nonce
# ---------------------------------------------------------------------------


def test_wrong_nonce(tmp_path):
    """nonce= changed in content → CANCELLATION_IDENTITY_MISMATCH, CONFLICTING_EVIDENCE,
    enforced NOT_DETERMINED."""
    run_dir = copy_fixture(FIXTURE_22, tmp_path)
    new_content = (
        "ControlProof CP-001-PRE denied this write: deny writes under protected/. "
        "nonce=BADNONCE0000000 event=PreToolUse"
    )
    patch_blocked_msg(run_dir, {"content": new_content})
    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")

    conflict_codes = [c.code for c in pre.facts.conflicts]
    assert "CANCELLATION_IDENTITY_MISMATCH" in conflict_codes
    assert result(pre) == CONTROL_CONFLICTING_EVIDENCE


# ---------------------------------------------------------------------------
# 7. test_wrong_event
# ---------------------------------------------------------------------------


def test_wrong_event(tmp_path):
    """event=PostToolUse in content → CANCELLATION_IDENTITY_MISMATCH, CONFLICTING_EVIDENCE."""
    run_dir = copy_fixture(FIXTURE_22, tmp_path)
    new_content = (
        "ControlProof CP-001-PRE denied this write: deny writes under protected/. "
        "nonce=5e1f0c2d9a7b3e41 event=PostToolUse"
    )
    patch_blocked_msg(run_dir, {"content": new_content})
    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")

    conflict_codes = [c.code for c in pre.facts.conflicts]
    assert "CANCELLATION_IDENTITY_MISMATCH" in conflict_codes
    assert result(pre) == CONTROL_CONFLICTING_EVIDENCE


# ---------------------------------------------------------------------------
# 8. test_wrong_signature_id
# ---------------------------------------------------------------------------


def test_wrong_signature_id(tmp_path):
    """signature.id differs from call id → not attributed."""
    run_dir = copy_fixture(FIXTURE_22, tmp_path)
    patch_signature(run_dir, {"id": "tooluse_WRONGID"})
    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.bob_cancelled_citing_control is not True


# ---------------------------------------------------------------------------
# 9. test_wrong_signature_name
# ---------------------------------------------------------------------------


def test_wrong_signature_name(tmp_path):
    """signature.name 'apply_diff' → not attributed."""
    run_dir = copy_fixture(FIXTURE_22, tmp_path)
    patch_signature(run_dir, {"name": "apply_diff"})
    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.bob_cancelled_citing_control is not True


# ---------------------------------------------------------------------------
# 10. test_ordinary_tool_error
# ---------------------------------------------------------------------------


def test_ordinary_tool_error(tmp_path):
    """Ordinary EACCES error with isError true → not attributed, PRE not ENFORCEMENT_VERIFIED."""
    run_dir = copy_fixture(FIXTURE_22, tmp_path)
    patch_blocked_msg(run_dir, {"content": "Failed to write file protected/test.txt: EACCES"})
    patch_signature(run_dir, {"isError": True})
    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.bob_cancelled_citing_control is not True
    assert result(pre) != CONTROL_ENFORCEMENT_VERIFIED


# ---------------------------------------------------------------------------
# 11. test_non_string_content
# ---------------------------------------------------------------------------


def test_non_string_content(tmp_path):
    """content as a list of text parts with isError true → no exception,
    bob_cancelled_citing_control NOT_DETERMINED."""
    run_dir = copy_fixture(FIXTURE_22, tmp_path)
    patch_blocked_msg(
        run_dir,
        {"content": [{"type": "text", "text": "ControlProof CP-001-PRE denied this write: x"}]},
    )
    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.bob_cancelled_citing_control is NOT_DETERMINED


# ---------------------------------------------------------------------------
# 12. test_missing_signature
# ---------------------------------------------------------------------------


def test_missing_signature(tmp_path):
    """toolUsage removed → not attributed (the 2.2.0 form needs Bob's call link),
    PRE NOT_DETERMINED."""
    run_dir = copy_fixture(FIXTURE_22, tmp_path)
    remove_tool_usage(run_dir)
    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.bob_cancelled_citing_control is not True
    assert result(pre) == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 13. test_unit_cancellation_for_call
# ---------------------------------------------------------------------------


def test_unit_cancellation_for_call():
    """Direct BobToolCall unit tests for cancellation_for_call."""
    # ---- Valid 2.1 form ----
    call_21 = BobToolCall(
        id="id-abc",
        name="write_file",
        arguments={"path": "protected/test.txt"},
        result=(
            "Tool call to write_file was cancelled: "
            "ControlProof CP-001-PRE denied this write: reason. "
            "nonce=abc123 event=PreToolUse"
        ),
    )
    r21 = cancellation_for_call(call_21)
    assert r21 is not None
    assert r21["form"] == "2.1"
    assert r21["control_id"] == "CP-001-PRE"
    assert r21["nonce"] == "abc123"
    assert r21["event"] == "PreToolUse"

    # ---- Valid 2.2 form ----
    call_22 = BobToolCall(
        id="tooluse_X",
        name="write_file",
        arguments={"path": "protected/test.txt"},
        result=(
            "ControlProof CP-001-PRE denied this write: reason. "
            "nonce=abc123 event=PreToolUse"
        ),
        result_is_error=True,
        result_signature_id="tooluse_X",
        result_signature_name="write_file",
    )
    r22 = cancellation_for_call(call_22)
    assert r22 is not None
    assert r22["form"] == "2.2"
    assert r22["control_id"] == "CP-001-PRE"
    assert r22["nonce"] == "abc123"
    assert r22["event"] == "PreToolUse"

    # ---- Each single broken condition of the 2.2 form returns None ----

    # result is not a string
    assert cancellation_for_call(dataclasses.replace(call_22, result=None)) is None

    # result_is_error is not exactly True (False)
    assert cancellation_for_call(dataclasses.replace(call_22, result_is_error=False)) is None

    # result_is_error is string "true"
    assert cancellation_for_call(dataclasses.replace(call_22, result_is_error="true")) is None

    # result_is_error is 1
    assert cancellation_for_call(dataclasses.replace(call_22, result_is_error=1)) is None

    # result_signature_id is None
    assert cancellation_for_call(dataclasses.replace(call_22, result_signature_id=None)) is None

    # result_signature_id is empty string
    assert cancellation_for_call(dataclasses.replace(call_22, result_signature_id="")) is None

    # result_signature_id differs from call.id
    assert cancellation_for_call(dataclasses.replace(call_22, result_signature_id="tooluse_OTHER")) is None

    # result_signature_name differs from call.name
    assert cancellation_for_call(dataclasses.replace(call_22, result_signature_name="apply_diff")) is None

    # content doesn't begin with ControlProof ... denied this write:
    assert cancellation_for_call(dataclasses.replace(
        call_22,
        result="Failed to write protected/test.txt: EACCES",
    )) is None
