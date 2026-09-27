"""Tests for controlproof.conflicts — task B3."""
from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from controlproof.conflicts import CONFLICT_AFFECTS, assess, detect_conflicts
from controlproof.ingest import ControlEvidence, ingest
from controlproof.model import (
    NOT_DETERMINED,
    CONTROL_CONFIGURED_NOT_EXECUTED,
    CONTROL_CONFLICTING_EVIDENCE,
    CONTROL_ENFORCEMENT_VERIFIED,
    CONTROL_NOT_CONFIGURED,
    CONTROL_NOT_DETERMINED,
    CONTROL_OBSERVATIONAL_ONLY,
    Conflict,
    ControlFacts,
    derive_states,
    evaluate,
)

FIXTURE = Path(__file__).parent / "fixtures" / "run-3arm"

SID_PRE = "3f9a1c7e5b2d4f60a8e1c3b5d7f90a12"
SID_POST = "7c2e4a6b8d0f1e3a5c7b9d1f3e5a7c90"
SID_BADCFG = "b4d6f8a0c2e4a6b8d0f2e4a6c8b0d2f4"


def copy_fixture(tmp_path: Path) -> Path:
    dst = tmp_path / "run-3arm"
    shutil.copytree(str(FIXTURE), str(dst))
    return dst


def get_ev(evs: list[ControlEvidence], control_id: str) -> ControlEvidence:
    for ev in evs:
        if ev.facts.control_id == control_id:
            return ev
    raise KeyError(control_id)


def result(ev: ControlEvidence):
    """Evaluate ev.facts with bundle_verified=True."""
    return evaluate(dataclasses.replace(ev.facts, bundle_verified=True))


def read_ledger(run_dir: Path) -> list[dict]:
    p = run_dir / "ledger.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def write_ledger(run_dir: Path, rows: list[dict]) -> None:
    (run_dir / "ledger.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 10. test_clean_arms_no_conflicts
# ---------------------------------------------------------------------------


def test_clean_arms_no_conflicts(tmp_path):
    run_dir = copy_fixture(tmp_path)
    evs = assess(run_dir)

    for ev in evs:
        assert ev.facts.conflicts == (), f"Expected no conflicts for {ev.facts.control_id}"

    pre = get_ev(evs, "CP-001-PRE")
    post = get_ev(evs, "CP-002-POST")
    badcfg = get_ev(evs, "CP-004-BADCFG")

    assert result(pre).result == CONTROL_ENFORCEMENT_VERIFIED
    assert result(post).result == CONTROL_OBSERVATIONAL_ONLY
    assert result(badcfg).result == CONTROL_CONFIGURED_NOT_EXECUTED


# ---------------------------------------------------------------------------
# 11. test_ledger_path_equal_after_normalization
# ---------------------------------------------------------------------------


def test_ledger_path_equal_after_normalization(tmp_path):
    """PRE protected row's path as absolute C:\\cp-run\\ws-pre\\protected\\test.txt: no conflict."""
    run_dir = copy_fixture(tmp_path)
    rows = read_ledger(run_dir)
    for row in rows:
        if (
            row.get("control_id") == "CP-001-PRE"
            and row.get("decision") == "DENY"
        ):
            row["tool_input_path"] = "C:\\cp-run\\ws-pre\\protected\\test.txt"
    write_ledger(run_dir, rows)

    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    conflict_codes = [c.code for c in pre.facts.conflicts]
    assert "LEDGER_PATH_MISMATCH" not in conflict_codes
    assert result(pre).result == CONTROL_ENFORCEMENT_VERIFIED


# ---------------------------------------------------------------------------
# 12. test_ledger_path_mismatch
# ---------------------------------------------------------------------------


def test_ledger_path_mismatch(tmp_path):
    """PRE probe row's tool_input_path rewritten to protected/test.txt."""
    run_dir = copy_fixture(tmp_path)
    rows = read_ledger(run_dir)
    for row in rows:
        if (
            row.get("control_id") == "CP-001-PRE"
            and row.get("decision") == "ALLOW"
            and row.get("tool_use_id") == "tooluse_wspreA1b2C3d4E5f6"
        ):
            row["tool_input_path"] = "protected/test.txt"
    write_ledger(run_dir, rows)

    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    conflict_codes = [c.code for c in pre.facts.conflicts]
    assert "LEDGER_PATH_MISMATCH" in conflict_codes
    rec = result(pre)
    assert rec.result == CONTROL_CONFLICTING_EVIDENCE
    assert rec.states["executed"] is NOT_DETERMINED
    assert rec.states["observed"] is NOT_DETERMINED
    assert rec.states["enforced"] is NOT_DETERMINED


# ---------------------------------------------------------------------------
# 13. test_cancellation_cites_other_control
# ---------------------------------------------------------------------------


def test_cancellation_cites_other_control(tmp_path):
    """Replace CP-001-PRE with CP-OTHER or CP-001-PRE-X in cancellation text."""
    for other_id in ("CP-OTHER", "CP-001-PRE-X"):
        run_dir = copy_fixture(tmp_path / other_id)
        task_path = run_dir / "bob_tasks" / f"{SID_PRE}.json"
        task_data = json.loads(task_path.read_text())
        for msg in task_data["messages"]:
            if msg.get("role") == "tool":
                content = msg.get("data", {}).get("content", "")
                if "CP-001-PRE" in content:
                    msg["data"]["content"] = content.replace("CP-001-PRE", other_id)
        task_path.write_text(json.dumps(task_data), encoding="utf-8")

        evs = assess(run_dir)
        pre = get_ev(evs, "CP-001-PRE")
        # bob_cancelled_citing_control must be False (not True)
        assert pre.facts.bob_cancelled_citing_control is False, \
            f"Expected False for {other_id!r}, got {pre.facts.bob_cancelled_citing_control!r}"
        # no conflict
        assert pre.facts.conflicts == ()
        # result NOT_DETERMINED (not ENFORCEMENT_VERIFIED)
        rec = result(pre)
        assert rec.result == CONTROL_NOT_DETERMINED, \
            f"Expected NOT_DETERMINED for {other_id!r}, got {rec.result!r}"


# ---------------------------------------------------------------------------
# 14. test_malformed_snapshot_files
# ---------------------------------------------------------------------------


def test_malformed_snapshot_files(tmp_path):
    """PRE snapshot files as a string, as [1,2], and missing: NOT_DETERMINED."""
    snap_path_tpl = FIXTURE.parent  # we'll rebuild each variant

    for variant_name, files_value, delete_key in [
        ("string", "not-a-list", False),
        ("int_list", [1, 2], False),
        ("missing", None, True),
    ]:
        run_dir = copy_fixture(tmp_path / variant_name)
        snap_path = run_dir / "snapshots" / "ws-pre.AFTER.json"
        snap_data = json.loads(snap_path.read_text())
        if delete_key:
            del snap_data["files"]
        else:
            snap_data["files"] = files_value
        snap_path.write_text(json.dumps(snap_data), encoding="utf-8")

        evs = assess(run_dir)
        pre = get_ev(evs, "CP-001-PRE")
        assert pre.facts.target_exists_after is NOT_DETERMINED, \
            f"variant={variant_name!r}: expected NOT_DETERMINED"
        rec = result(pre)
        assert rec.result == CONTROL_NOT_DETERMINED, \
            f"variant={variant_name!r}: expected NOT_DETERMINED result"


# ---------------------------------------------------------------------------
# 15. test_exact_duplicate_row_tolerated
# ---------------------------------------------------------------------------


def test_exact_duplicate_row_tolerated(tmp_path):
    """Append exact copy of PRE's DENY row: no DUPLICATE_DISAGREES, ledger_rows 2."""
    run_dir = copy_fixture(tmp_path)
    rows = read_ledger(run_dir)
    # Find PRE DENY row and append a copy
    deny_row = next(
        r for r in rows
        if r.get("control_id") == "CP-001-PRE" and r.get("decision") == "DENY"
    )
    rows.append(dict(deny_row))
    write_ledger(run_dir, rows)

    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    conflict_codes = [c.code for c in pre.facts.conflicts]
    assert "DUPLICATE_DISAGREES" not in conflict_codes
    # 2 distinct non-empty tool_use_ids (probe + protected)
    assert pre.facts.ledger_rows == 2
    assert result(pre).result == CONTROL_ENFORCEMENT_VERIFIED


# ---------------------------------------------------------------------------
# 16. test_disagreeing_duplicate_row
# ---------------------------------------------------------------------------


def test_disagreeing_duplicate_row(tmp_path):
    """Append PRE's DENY row with decision ALLOW: DUPLICATE_DISAGREES."""
    run_dir = copy_fixture(tmp_path)
    rows = read_ledger(run_dir)
    deny_row = next(
        r for r in rows
        if r.get("control_id") == "CP-001-PRE" and r.get("decision") == "DENY"
    )
    modified_row = dict(deny_row, decision="ALLOW")
    rows.append(modified_row)
    write_ledger(run_dir, rows)

    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    conflict_codes = [c.code for c in pre.facts.conflicts]
    assert "DUPLICATE_DISAGREES" in conflict_codes
    assert result(pre).result == CONTROL_CONFLICTING_EVIDENCE


# ---------------------------------------------------------------------------
# 17. test_non_string_tool_result
# ---------------------------------------------------------------------------


def test_non_string_tool_result(tmp_path):
    """Non-string tool result: no exception, bob_cancelled NOT_DETERMINED."""
    cancellation_text = (
        "Tool call to write_file was cancelled: "
        "ControlProof CP-001-PRE denied this write: deny writes under protected/."
        " nonce=5e1f0c2d9a7b3e41 event=PreToolUse"
    )
    for variant_name, content_value in [
        ("list", [{"type": "text", "text": cancellation_text}]),
        ("dict", {"type": "text", "text": cancellation_text}),
    ]:
        run_dir = copy_fixture(tmp_path / variant_name)
        task_path = run_dir / "bob_tasks" / f"{SID_PRE}.json"
        task_data = json.loads(task_path.read_text())
        for msg in task_data["messages"]:
            if msg.get("role") == "tool":
                content = msg.get("data", {}).get("content", "")
                if isinstance(content, str) and "CP-001-PRE" in content:
                    msg["data"]["content"] = content_value
        task_path.write_text(json.dumps(task_data), encoding="utf-8")

        evs = assess(run_dir)
        pre = get_ev(evs, "CP-001-PRE")
        assert pre.facts.bob_cancelled_citing_control is NOT_DETERMINED, \
            f"variant={variant_name!r}"
        rec = result(pre)
        assert rec.result != CONTROL_ENFORCEMENT_VERIFIED, \
            f"variant={variant_name!r}: should not be ENFORCEMENT_VERIFIED"


# ---------------------------------------------------------------------------
# 18. test_configured_vs_actual_event
# ---------------------------------------------------------------------------


def test_configured_vs_actual_event(tmp_path):
    # Part A: PRE settings key changed to PostToolUse
    run_dir_a = copy_fixture(tmp_path / "a")
    settings_path = run_dir_a / "configs" / "ws-pre.settings.json"
    settings = json.loads(settings_path.read_text())
    hooks_section = settings.get("hooks", {})
    if "PreToolUse" in hooks_section:
        hooks_section["PostToolUse"] = hooks_section.pop("PreToolUse")
    settings["hooks"] = hooks_section
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    evs_a = assess(run_dir_a)
    pre_a = get_ev(evs_a, "CP-001-PRE")
    conflict_codes_a = [c.code for c in pre_a.facts.conflicts]
    assert "LIFECYCLE_MISMATCH" in conflict_codes_a
    assert result(pre_a).result == CONTROL_CONFLICTING_EVIDENCE
    rec_a = result(pre_a)
    assert rec_a.states["enforceable"] is NOT_DETERMINED

    # Part B: One PRE row changed to PostToolUse → MULTIPLE_EVENTS and LIFECYCLE_MISMATCH
    run_dir_b = copy_fixture(tmp_path / "b")
    rows = read_ledger(run_dir_b)
    changed = False
    for row in rows:
        if (
            row.get("control_id") == "CP-001-PRE"
            and not changed
        ):
            row["hook_event_name"] = "PostToolUse"
            changed = True
    write_ledger(run_dir_b, rows)

    evs_b = assess(run_dir_b)
    pre_b = get_ev(evs_b, "CP-001-PRE")
    conflict_codes_b = [c.code for c in pre_b.facts.conflicts]
    assert "MULTIPLE_EVENTS" in conflict_codes_b
    assert "LIFECYCLE_MISMATCH" in conflict_codes_b


# ---------------------------------------------------------------------------
# 19. test_cancelled_but_target_exists
# ---------------------------------------------------------------------------


def test_cancelled_but_target_exists(tmp_path):
    """Add protected/test.txt to PRE's snapshot: CANCELLED_BUT_TARGET_EXISTS."""
    run_dir = copy_fixture(tmp_path)
    snap_path = run_dir / "snapshots" / "ws-pre.AFTER.json"
    snap_data = json.loads(snap_path.read_text())
    snap_data["files"].append("protected/test.txt")
    snap_path.write_text(json.dumps(snap_data), encoding="utf-8")

    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    conflict_codes = [c.code for c in pre.facts.conflicts]
    assert "CANCELLED_BUT_TARGET_EXISTS" in conflict_codes
    rec = result(pre)
    assert rec.result == CONTROL_CONFLICTING_EVIDENCE
    assert rec.states["enforced"] is NOT_DETERMINED
    # enforceable should still be True (CANCELLED_BUT_TARGET_EXISTS doesn't affect it)
    assert rec.states["enforceable"] is True


# ---------------------------------------------------------------------------
# 20. test_cancellation_identity_mismatch
# ---------------------------------------------------------------------------


def test_cancellation_identity_mismatch(tmp_path):
    """PRE cancellation nonce= changed, and separately event=PostToolUse."""
    original_text = (
        "Tool call to write_file was cancelled: "
        "ControlProof CP-001-PRE denied this write: deny writes under protected/."
        " nonce=5e1f0c2d9a7b3e41 event=PreToolUse"
    )

    for variant_name, new_text in [
        ("nonce", original_text.replace("nonce=5e1f0c2d9a7b3e41", "nonce=WRONGNONCE")),
        ("event", original_text.replace("event=PreToolUse", "event=PostToolUse")),
    ]:
        run_dir = copy_fixture(tmp_path / variant_name)
        task_path = run_dir / "bob_tasks" / f"{SID_PRE}.json"
        task_data = json.loads(task_path.read_text())
        for msg in task_data["messages"]:
            if msg.get("role") == "tool":
                content = msg.get("data", {}).get("content", "")
                if "CP-001-PRE" in content:
                    msg["data"]["content"] = new_text
        task_path.write_text(json.dumps(task_data), encoding="utf-8")

        evs = assess(run_dir)
        pre = get_ev(evs, "CP-001-PRE")
        conflict_codes = [c.code for c in pre.facts.conflicts]
        assert "CANCELLATION_IDENTITY_MISMATCH" in conflict_codes, \
            f"variant={variant_name!r}: expected CANCELLATION_IDENTITY_MISMATCH"
        assert result(pre).result == CONTROL_CONFLICTING_EVIDENCE, \
            f"variant={variant_name!r}"


# ---------------------------------------------------------------------------
# 21. test_hook_digest_mismatch
# ---------------------------------------------------------------------------


def test_hook_digest_mismatch(tmp_path):
    # Part A: declared sha="1"*64 → conflict on PRE and POST, not on BADCFG
    run_dir_a = copy_fixture(tmp_path / "a")
    run_data = json.loads((run_dir_a / "RUN.json").read_text())
    run_data["hook"] = {"sha256": "1" * 64}
    (run_dir_a / "RUN.json").write_text(json.dumps(run_data), encoding="utf-8")

    evs_a = assess(run_dir_a)
    pre_a = get_ev(evs_a, "CP-001-PRE")
    post_a = get_ev(evs_a, "CP-002-POST")
    badcfg_a = get_ev(evs_a, "CP-004-BADCFG")
    assert any(c.code == "HOOK_DIGEST_MISMATCH" for c in pre_a.facts.conflicts)
    assert any(c.code == "HOOK_DIGEST_MISMATCH" for c in post_a.facts.conflicts)
    assert not any(c.code == "HOOK_DIGEST_MISMATCH" for c in badcfg_a.facts.conflicts)

    # Part B: declared sha="0"*64 → no conflict (rows already have "00...0")
    run_dir_b = copy_fixture(tmp_path / "b")
    run_data_b = json.loads((run_dir_b / "RUN.json").read_text())
    run_data_b["hook"] = {"sha256": "0" * 64}
    (run_dir_b / "RUN.json").write_text(json.dumps(run_data_b), encoding="utf-8")

    evs_b = assess(run_dir_b)
    pre_b = get_ev(evs_b, "CP-001-PRE")
    assert not any(c.code == "HOOK_DIGEST_MISMATCH" for c in pre_b.facts.conflicts)

    # Part C: two distinct digests among PRE rows (no declared sha)
    run_dir_c = copy_fixture(tmp_path / "c")
    rows = read_ledger(run_dir_c)
    changed = False
    for row in rows:
        if row.get("control_id") == "CP-001-PRE" and not changed:
            row["hook_sha256"] = "a" * 64
            changed = True
    write_ledger(run_dir_c, rows)

    evs_c = assess(run_dir_c)
    pre_c = get_ev(evs_c, "CP-001-PRE")
    assert any(c.code == "HOOK_DIGEST_MISMATCH" for c in pre_c.facts.conflicts)


# ---------------------------------------------------------------------------
# 22. test_forged_rows_despite_invalid_matcher
# ---------------------------------------------------------------------------


def test_forged_rows_despite_invalid_matcher(tmp_path):
    """Add two BADCFG rows matching its Bob calls: LEDGER_CONTRADICTS_INVALID_MATCHER."""
    run_dir = copy_fixture(tmp_path)
    rows = read_ledger(run_dir)
    # BADCFG Bob calls:
    # tooluse_wsbadcfgA1b2C3d4E5f6 → write_file allowed/probe.txt
    # tooluse_wsbadcfgG7h8J9k0L1m2 → write_file protected/test.txt
    badcfg_rows = [
        {
            "schema": "controlproof.ledger/1",
            "control_id": "CP-004-BADCFG",
            "run_nonce": "5e1f0c2d9a7b3e41",
            "session_id": SID_BADCFG,
            "hook_event_name": "PreToolUse",
            "tool_name": "write_file",
            "tool_use_id": "tooluse_wsbadcfgA1b2C3d4E5f6",
            "tool_input_path": "allowed/probe.txt",
            "decision": "ALLOW",
            "policy_triggered": False,
            "has_tool_response": False,
            "exit_code": 0,
            "hook_sha256": "0" * 64,
            "hook_invoked_at": "2026-09-26T08:06:25.000Z",
        },
        {
            "schema": "controlproof.ledger/1",
            "control_id": "CP-004-BADCFG",
            "run_nonce": "5e1f0c2d9a7b3e41",
            "session_id": SID_BADCFG,
            "hook_event_name": "PreToolUse",
            "tool_name": "write_file",
            "tool_use_id": "tooluse_wsbadcfgG7h8J9k0L1m2",
            "tool_input_path": "protected/test.txt",
            "decision": "DENY",
            "policy_triggered": True,
            "has_tool_response": False,
            "exit_code": 2,
            "hook_sha256": "0" * 64,
            "hook_invoked_at": "2026-09-26T08:06:26.000Z",
        },
    ]
    rows.extend(badcfg_rows)
    write_ledger(run_dir, rows)

    evs = assess(run_dir)
    badcfg = get_ev(evs, "CP-004-BADCFG")
    conflict_codes = [c.code for c in badcfg.facts.conflicts]
    assert "LEDGER_CONTRADICTS_INVALID_MATCHER" in conflict_codes
    assert result(badcfg).result == CONTROL_CONFLICTING_EVIDENCE


# ---------------------------------------------------------------------------
# 23. test_not_configured_but_ran
# ---------------------------------------------------------------------------


def test_not_configured_but_ran(tmp_path):
    """Remove PRE's hook entry from its settings: NOT_CONFIGURED_BUT_RAN."""
    run_dir = copy_fixture(tmp_path)
    settings_path = run_dir / "configs" / "ws-pre.settings.json"
    settings = json.loads(settings_path.read_text())
    # Remove the PRE hook entry
    for event, entries in settings.get("hooks", {}).items():
        settings["hooks"][event] = [
            e for e in entries
            if not any(
                "CP-001-PRE" in t.strip('"')
                for hook in e.get("hooks", [])
                for t in hook.get("command", "").split()
            )
        ]
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    evs = assess(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    conflict_codes = [c.code for c in pre.facts.conflicts]
    assert "NOT_CONFIGURED_BUT_RAN" in conflict_codes
    assert result(pre).result == CONTROL_CONFLICTING_EVIDENCE


# ---------------------------------------------------------------------------
# 24. test_bob_version_mismatch
# ---------------------------------------------------------------------------


def test_bob_version_mismatch(tmp_path):
    """RUN.json bob_version = 2.1.0: BOB_VERSION_MISMATCH."""
    run_dir = copy_fixture(tmp_path)
    run_data = json.loads((run_dir / "RUN.json").read_text())
    run_data["bob_version"] = "2.1.0"
    (run_dir / "RUN.json").write_text(json.dumps(run_data), encoding="utf-8")

    evs = assess(run_dir)
    for ev in evs:
        conflict_codes = [c.code for c in ev.facts.conflicts]
        assert "BOB_VERSION_MISMATCH" in conflict_codes, \
            f"Expected BOB_VERSION_MISMATCH for {ev.facts.control_id}"


# ---------------------------------------------------------------------------
# 25. test_missing_evidence_is_not_conflict
# ---------------------------------------------------------------------------


def test_missing_evidence_is_not_conflict(tmp_path):
    """Missing ledger, PRE snapshot, PRE bob task: no conflicts, PRE result NOT_DETERMINED."""
    # Sub-test A: delete ledger.jsonl
    for variant_name, delete_fn in [
        ("ledger", lambda d: (d / "ledger.jsonl").unlink()),
        ("snapshot", lambda d: (d / "snapshots" / "ws-pre.AFTER.json").unlink()),
        ("bobtask", lambda d: (d / "bob_tasks" / f"{SID_PRE}.json").unlink()),
    ]:
        run_dir = copy_fixture(tmp_path / variant_name)
        delete_fn(run_dir)

        evs = assess(run_dir)
        pre = get_ev(evs, "CP-001-PRE")
        assert pre.facts.conflicts == (), \
            f"variant={variant_name!r}: expected no conflicts, got {pre.facts.conflicts}"
        rec = result(pre)
        assert rec.result == CONTROL_NOT_DETERMINED, \
            f"variant={variant_name!r}: expected NOT_DETERMINED, got {rec.result}"


# ---------------------------------------------------------------------------
# 26. test_conflict_table
# ---------------------------------------------------------------------------


def test_conflict_table():
    """CONFLICT_AFFECTS has exactly the ten codes; certain codes don't affect enforceable."""
    expected_codes = {
        "LIFECYCLE_MISMATCH",
        "MULTIPLE_EVENTS",
        "LEDGER_PATH_MISMATCH",
        "DUPLICATE_DISAGREES",
        "CANCELLED_BUT_TARGET_EXISTS",
        "CANCELLATION_IDENTITY_MISMATCH",
        "HOOK_DIGEST_MISMATCH",
        "LEDGER_CONTRADICTS_INVALID_MATCHER",
        "NOT_CONFIGURED_BUT_RAN",
        "BOB_VERSION_MISMATCH",
    }
    assert set(CONFLICT_AFFECTS.keys()) == expected_codes

    no_enforceable = {
        "LEDGER_PATH_MISMATCH",
        "DUPLICATE_DISAGREES",
        "CANCELLED_BUT_TARGET_EXISTS",
        "CANCELLATION_IDENTITY_MISMATCH",
        "NOT_CONFIGURED_BUT_RAN",
    }
    for code in no_enforceable:
        assert "enforceable" not in CONFLICT_AFFECTS[code], \
            f"{code} should not affect enforceable"


# ---------------------------------------------------------------------------
# 27. test_conflict_withdraws_states
# ---------------------------------------------------------------------------


def test_conflict_withdraws_states():
    """Conflict with affects=('observed',) makes observed NOT_DETERMINED; 'banana' raises."""
    facts = ControlFacts(
        control_id="CP-TEST",
        bundle_verified=True,
        bob_version="2.2.0",
        configured=True,
        ledger_rows=1,
        ledger_corroborated=True,
        actual_event="PreToolUse",
        target_in_payload=True,
        policy_triggered=True,
        bob_cancelled_citing_control=True,
        target_exists_after=False,
        conflicts=(Conflict(
            code="FAKE",
            artifacts=("ledger", "ledger"),
            detail="test conflict",
            affects=("observed",),
        ),),
    )
    states = derive_states(facts)
    assert states["observed"] is NOT_DETERMINED
    # Other states unaffected
    assert states["configured"] is True
    assert states["executed"] is True

    # Bad affects raises TypeError
    with pytest.raises(TypeError):
        Conflict(
            code="BAD",
            artifacts=("ledger",),
            detail="bad",
            affects=("banana",),
        )


# ---------------------------------------------------------------------------
# 28. test_ingest_contract_unchanged
# ---------------------------------------------------------------------------


def test_ingest_contract_unchanged(tmp_path):
    """ingest on the clean fixture returns conflicts == () for every control."""
    run_dir = copy_fixture(tmp_path)
    evs = ingest(run_dir)
    for ev in evs:
        assert ev.facts.conflicts == (), \
            f"ingest should return empty conflicts for {ev.facts.control_id}"
