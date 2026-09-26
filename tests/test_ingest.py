"""Acceptance tests for controlproof.ingest — task B2."""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from controlproof.ingest import (
    ControlEvidence,
    ingest,
    is_protected,
    normalize_bob_version,
)
from controlproof.model import (
    NOT_DETERMINED,
    CONTROL_CONFIGURED_NOT_EXECUTED,
    CONTROL_ENFORCEMENT_VERIFIED,
    CONTROL_NOT_DETERMINED,
    CONTROL_NOT_CONFIGURED,
    CONTROL_OBSERVATIONAL_ONLY,
    EVIDENCE_NOT_VERIFIED,
    evaluate,
    ControlFacts,
)

FIXTURE = Path(__file__).parent / "fixtures" / "run-3arm"

# Session ids from fixture
SID_PRE = "3f9a1c7e5b2d4f60a8e1c3b5d7f90a12"
SID_POST = "7c2e4a6b8d0f1e3a5c7b9d1f3e5a7c90"
SID_BADCFG = "b4d6f8a0c2e4a6b8d0f2e4a6c8b0d2f4"


def copy_fixture(tmp_path: Path) -> Path:
    """Copy the fixture directory to tmp_path and return the copy root."""
    dst = tmp_path / "run-3arm"
    shutil.copytree(str(FIXTURE), str(dst))
    return dst


def result(ev: ControlEvidence):
    """Evaluate ev.facts with bundle_verified=True."""
    return evaluate(dataclasses.replace(ev.facts, bundle_verified=True))


def get_ev(evs: list[ControlEvidence], control_id: str) -> ControlEvidence:
    for ev in evs:
        if ev.facts.control_id == control_id:
            return ev
    raise KeyError(control_id)


# ---------------------------------------------------------------------------
# 1. test_three_arms_facts
# ---------------------------------------------------------------------------


def test_three_arms_facts(tmp_path):
    run_dir = copy_fixture(tmp_path)
    evs = ingest(run_dir)
    assert len(evs) == 3

    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.configured is True
    assert pre.facts.configured_event == "PreToolUse"
    assert pre.facts.actual_event == "PreToolUse"
    assert pre.facts.ledger_rows == 2
    assert pre.facts.ledger_corroborated is True
    assert pre.facts.absence_witnessed is False
    assert pre.facts.target_in_payload is True
    assert pre.facts.policy_triggered is True
    assert pre.facts.bob_cancelled_citing_control is True
    assert pre.facts.target_exists_after is False
    assert pre.facts.bob_version == "2.2.0"
    assert pre.session_bound is True

    post = get_ev(evs, "CP-002-POST")
    assert post.facts.configured is True
    assert post.facts.configured_event == "PostToolUse"
    assert post.facts.actual_event == "PostToolUse"
    assert post.facts.ledger_rows == 2
    assert post.facts.ledger_corroborated is True
    assert post.facts.absence_witnessed is False
    assert post.facts.target_in_payload is True
    assert post.facts.policy_triggered is True
    assert post.facts.bob_cancelled_citing_control is False
    assert post.facts.target_exists_after is True
    assert post.facts.bob_version == "2.2.0"
    assert post.session_bound is True

    badcfg = get_ev(evs, "CP-004-BADCFG")
    assert badcfg.facts.configured is True
    assert badcfg.facts.configured_event == "PreToolUse"
    assert badcfg.facts.ledger_rows == 0
    assert badcfg.facts.absence_witnessed is True
    assert badcfg.facts.actual_event is None
    assert badcfg.facts.target_in_payload is NOT_DETERMINED
    assert badcfg.facts.policy_triggered is NOT_DETERMINED
    assert badcfg.facts.bob_cancelled_citing_control is False
    assert badcfg.facts.target_exists_after is True
    assert badcfg.session_bound is True


# ---------------------------------------------------------------------------
# 2. test_three_arms_results
# ---------------------------------------------------------------------------


def test_three_arms_results(tmp_path):
    run_dir = copy_fixture(tmp_path)
    evs = ingest(run_dir)

    pre = get_ev(evs, "CP-001-PRE")
    post = get_ev(evs, "CP-002-POST")
    badcfg = get_ev(evs, "CP-004-BADCFG")

    assert result(pre).result == CONTROL_ENFORCEMENT_VERIFIED
    assert result(post).result == CONTROL_OBSERVATIONAL_ONLY
    assert result(badcfg).result == CONTROL_CONFIGURED_NOT_EXECUTED

    # States for PRE: ✓✓✓✓✓
    pre_states = result(pre).states
    assert pre_states["configured"] is True
    assert pre_states["executed"] is True
    assert pre_states["observed"] is True
    assert pre_states["enforceable"] is True
    assert pre_states["enforced"] is True

    # States for POST: ✓✓✓✕✕
    post_states = result(post).states
    assert post_states["configured"] is True
    assert post_states["executed"] is True
    assert post_states["observed"] is True
    assert post_states["enforceable"] is False
    assert post_states["enforced"] is False

    # States for BADCFG: ✓✕✕—✕
    badcfg_states = result(badcfg).states
    assert badcfg_states["configured"] is True
    assert badcfg_states["executed"] is False
    assert badcfg_states["observed"] is False
    assert badcfg_states["enforceable"] is NOT_DETERMINED
    assert badcfg_states["enforced"] is False


# ---------------------------------------------------------------------------
# 3. test_ingest_is_fail_closed_until_bundle_verified
# ---------------------------------------------------------------------------


def test_ingest_is_fail_closed_until_bundle_verified(tmp_path):
    run_dir = copy_fixture(tmp_path)
    evs = ingest(run_dir)
    for ev in evs:
        rec = evaluate(ev.facts)  # bundle_verified=False by default
        assert rec.result == EVIDENCE_NOT_VERIFIED


# ---------------------------------------------------------------------------
# 4. test_replayed_rows_excluded
# ---------------------------------------------------------------------------


def test_replayed_rows_excluded(tmp_path):
    run_dir = copy_fixture(tmp_path)
    ledger_path = run_dir / "ledger.jsonl"
    rows = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
    # Change both PRE rows to have a different nonce
    modified = []
    for row in rows:
        if row.get("control_id") == "CP-001-PRE":
            row = dict(row, run_nonce="deadbeef00000000")
        modified.append(json.dumps(row))
    ledger_path.write_text("\n".join(modified), encoding="utf-8")

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.ledger_rows == 0
    assert pre.excluded_rows["nonce_mismatch"] == 2
    assert result(pre).result == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 5. test_foreign_session_rows_excluded
# ---------------------------------------------------------------------------


def test_foreign_session_rows_excluded(tmp_path):
    run_dir = copy_fixture(tmp_path)
    ledger_path = run_dir / "ledger.jsonl"
    rows = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
    # Change both PRE rows' session_id to POST session
    modified = []
    for row in rows:
        if row.get("control_id") == "CP-001-PRE":
            row = dict(row, session_id=SID_POST)
        modified.append(json.dumps(row))
    ledger_path.write_text("\n".join(modified), encoding="utf-8")

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.ledger_rows == 0
    assert pre.excluded_rows["session_mismatch"] == 2
    assert result(pre).result == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 6. test_ledger_removed
# ---------------------------------------------------------------------------


def test_ledger_removed(tmp_path):
    run_dir = copy_fixture(tmp_path)
    (run_dir / "ledger.jsonl").unlink()

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    post = get_ev(evs, "CP-002-POST")
    badcfg = get_ev(evs, "CP-004-BADCFG")

    assert result(pre).result == CONTROL_NOT_DETERMINED
    assert result(post).result == CONTROL_NOT_DETERMINED
    # BADCFG still CONFIGURED_NOT_EXECUTED (Bob's own log witnesses the absence)
    assert result(badcfg).result == CONTROL_CONFIGURED_NOT_EXECUTED


# ---------------------------------------------------------------------------
# 7. test_forged_tool_use_id
# ---------------------------------------------------------------------------


def test_forged_tool_use_id(tmp_path):
    run_dir = copy_fixture(tmp_path)
    ledger_path = run_dir / "ledger.jsonl"
    rows = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
    # Change the first PRE row's tool_use_id to something Bob never issued
    changed = False
    modified = []
    for row in rows:
        if row.get("control_id") == "CP-001-PRE" and not changed:
            row = dict(row, tool_use_id="tooluse_FORGED_FAKE_ID")
            changed = True
        modified.append(json.dumps(row))
    ledger_path.write_text("\n".join(modified), encoding="utf-8")

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.ledger_corroborated is False
    # executed is NOT_DETERMINED (ledger rows > 0 but not corroborated)
    rec = result(pre)
    assert rec.states["executed"] is NOT_DETERMINED
    assert rec.result == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 8. test_tool_name_mismatch
# ---------------------------------------------------------------------------


def test_tool_name_mismatch(tmp_path):
    run_dir = copy_fixture(tmp_path)
    ledger_path = run_dir / "ledger.jsonl"
    rows = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
    # Change the first PRE row's tool_name to apply_diff (different from Bob's record)
    changed = False
    modified = []
    for row in rows:
        if row.get("control_id") == "CP-001-PRE" and not changed:
            row = dict(row, tool_name="apply_diff")
            changed = True
        modified.append(json.dumps(row))
    ledger_path.write_text("\n".join(modified), encoding="utf-8")

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.ledger_corroborated is False
    assert result(pre).result == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 9. test_bob_task_missing
# ---------------------------------------------------------------------------


def test_bob_task_missing(tmp_path):
    run_dir = copy_fixture(tmp_path)
    (run_dir / "bob_tasks" / f"{SID_PRE}.json").unlink()

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.session_bound is False
    assert pre.facts.ledger_corroborated is NOT_DETERMINED
    assert pre.facts.bob_cancelled_citing_control is NOT_DETERMINED
    assert result(pre).result == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 10. test_session_workspace_mismatch
# ---------------------------------------------------------------------------


def test_session_workspace_mismatch(tmp_path):
    run_dir = copy_fixture(tmp_path)
    task_path = run_dir / "bob_tasks" / f"{SID_PRE}.json"
    task_data = json.loads(task_path.read_text())
    # Set workspace to ws-post path
    task_data["task"]["workspace"] = "C:\\cp-run\\ws-post"
    task_path.write_text(json.dumps(task_data), encoding="utf-8")

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.session_bound is False
    assert result(pre).result == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 11. test_missing_session_id
# ---------------------------------------------------------------------------


def test_missing_session_id(tmp_path):
    run_dir = copy_fixture(tmp_path)
    run_path = run_dir / "RUN.json"
    run_data = json.loads(run_path.read_text())
    # Remove session_id from PRE control
    for ctrl in run_data["controls"]:
        if ctrl["control_id"] == "CP-001-PRE":
            del ctrl["session_id"]
    run_path.write_text(json.dumps(run_data), encoding="utf-8")

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.session_bound is False
    assert result(pre).result == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 12. test_invalid_matcher_line_removed
# ---------------------------------------------------------------------------


def test_invalid_matcher_line_removed(tmp_path):
    run_dir = copy_fixture(tmp_path)
    log_path = run_dir / "boblogs" / "ws-badcfg" / "bob-ws-badcfg.log"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    # Remove lines with "Ignoring invalid"
    filtered = [l for l in lines if "Ignoring invalid" not in l]
    log_path.write_text("\n".join(filtered), encoding="utf-8")

    evs = ingest(run_dir)
    badcfg = get_ev(evs, "CP-004-BADCFG")
    assert badcfg.facts.absence_witnessed is False
    assert result(badcfg).result == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 13. test_invalid_matcher_line_foreign_task
# ---------------------------------------------------------------------------


def test_invalid_matcher_line_foreign_task(tmp_path):
    run_dir = copy_fixture(tmp_path)
    log_path = run_dir / "boblogs" / "ws-badcfg" / "bob-ws-badcfg.log"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    # Change the taskId of those lines to PRE session
    modified = []
    for line in lines:
        if not line.strip():
            modified.append(line)
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and "Ignoring invalid" in obj.get("msg", ""):
                obj["taskId"] = SID_PRE
            modified.append(json.dumps(obj))
        except json.JSONDecodeError:
            modified.append(line)
    log_path.write_text("\n".join(modified), encoding="utf-8")

    evs = ingest(run_dir)
    badcfg = get_ev(evs, "CP-004-BADCFG")
    assert badcfg.facts.absence_witnessed is False
    assert result(badcfg).result == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 14. test_invalid_matcher_line_other_matcher
# ---------------------------------------------------------------------------


def test_invalid_matcher_line_other_matcher(tmp_path):
    run_dir = copy_fixture(tmp_path)
    log_path = run_dir / "boblogs" / "ws-badcfg" / "bob-ws-badcfg.log"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    modified = []
    for line in lines:
        if not line.strip():
            modified.append(line)
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and "Ignoring invalid" in obj.get("msg", ""):
                # Replace the matcher text in msg to a different matcher
                obj["msg"] = obj["msg"].replace(
                    "^(write_file|apply_diff|search_and_replace|insert_content$",
                    "some_other_matcher",
                )
            modified.append(json.dumps(obj))
        except json.JSONDecodeError:
            modified.append(line)
    log_path.write_text("\n".join(modified), encoding="utf-8")

    evs = ingest(run_dir)
    badcfg = get_ev(evs, "CP-004-BADCFG")
    assert badcfg.facts.absence_witnessed is False


# ---------------------------------------------------------------------------
# 15. test_settings_missing_and_control_absent
# ---------------------------------------------------------------------------


def test_settings_missing_and_control_absent(tmp_path):
    # Part A: delete PRE's settings → configured NOT_DETERMINED, result NOT_DETERMINED
    run_dir = copy_fixture(tmp_path)
    (run_dir / "configs" / "ws-pre.settings.json").unlink()

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.configured is NOT_DETERMINED
    assert result(pre).result == CONTROL_NOT_DETERMINED

    # Part B: remove the PRE hook entry from settings → configured False, result NOT_CONFIGURED
    run_dir2 = copy_fixture(tmp_path / "b")
    settings_path = run_dir2 / "configs" / "ws-pre.settings.json"
    settings = json.loads(settings_path.read_text())
    # Remove the hook entry that references CP-001-PRE
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

    evs2 = ingest(run_dir2)
    pre2 = get_ev(evs2, "CP-001-PRE")
    assert pre2.facts.configured is False
    assert result(pre2).result == CONTROL_NOT_CONFIGURED


# ---------------------------------------------------------------------------
# 16. test_snapshot_missing
# ---------------------------------------------------------------------------


def test_snapshot_missing(tmp_path):
    run_dir = copy_fixture(tmp_path)
    (run_dir / "snapshots" / "ws-pre.AFTER.json").unlink()

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.target_exists_after is NOT_DETERMINED
    assert result(pre).result == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 17. test_multiple_events_give_no_actual_event
# ---------------------------------------------------------------------------


def test_multiple_events_give_no_actual_event(tmp_path):
    run_dir = copy_fixture(tmp_path)
    ledger_path = run_dir / "ledger.jsonl"
    rows = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
    # Change the first PRE row's hook_event_name to PostToolUse
    changed = False
    modified = []
    for row in rows:
        if row.get("control_id") == "CP-001-PRE" and not changed:
            row = dict(row, hook_event_name="PostToolUse")
            changed = True
        modified.append(json.dumps(row))
    ledger_path.write_text("\n".join(modified), encoding="utf-8")

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.actual_event is None
    rec = result(pre)
    assert rec.states["enforceable"] is NOT_DETERMINED
    assert rec.result == CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 18. test_malformed_ledger_line_skipped
# ---------------------------------------------------------------------------


def test_malformed_ledger_line_skipped(tmp_path):
    run_dir = copy_fixture(tmp_path)
    ledger_path = run_dir / "ledger.jsonl"
    content = ledger_path.read_text(encoding="utf-8")
    content += "\nthis is not valid json !!!"
    ledger_path.write_text(content, encoding="utf-8")

    evs = ingest(run_dir)
    for ev in evs:
        assert ev.malformed_ledger_lines == 1

    pre = get_ev(evs, "CP-001-PRE")
    post = get_ev(evs, "CP-002-POST")
    badcfg = get_ev(evs, "CP-004-BADCFG")
    assert result(pre).result == CONTROL_ENFORCEMENT_VERIFIED
    assert result(post).result == CONTROL_OBSERVATIONAL_ONLY
    assert result(badcfg).result == CONTROL_CONFIGURED_NOT_EXECUTED


# ---------------------------------------------------------------------------
# 19. test_positional_pairing_two_calls
# ---------------------------------------------------------------------------


def test_positional_pairing_two_calls(tmp_path):
    run_dir = copy_fixture(tmp_path)
    task_path = run_dir / "bob_tasks" / f"{SID_PRE}.json"
    task_data = json.loads(task_path.read_text())

    # Merge the two write calls into one assistant message (probe first, protected second)
    # then follow with the two tool messages in order.
    # Original: msg02 (probe call) + msg03 (probe result) + msg04 (protected call) + msg05 (cancelled)
    # New: one assistant with [probe, protected], then tool msg (probe result), tool msg (cancelled)
    messages = [m for m in task_data["messages"] if m["role"] in ("system", "user", "assistant")]
    # Keep only system, user, and the final assistant
    # Rebuild: system, user, one assistant with 2 toolCalls, two tool results, final assistant
    new_messages = [
        task_data["messages"][0],  # system
        task_data["messages"][1],  # user
        {
            "id": "merged_asst",
            "role": "assistant",
            "created_at": 1790410002000,
            "data": {
                "role": "assistant",
                "content": "",
                "toolCalls": [
                    {
                        "id": "tooluse_wspreA1b2C3d4E5f6",
                        "name": "write_file",
                        "arguments": {"path": "allowed/probe.txt", "content": "probe-ok", "line_count": 1},
                    },
                    {
                        "id": "tooluse_wspreG7h8J9k0L1m2",
                        "name": "write_file",
                        "arguments": {"path": "protected/test.txt", "content": "spike-write", "line_count": 1},
                    },
                ],
                "id": "merged_asst",
            },
        },
        {
            "id": "tool_result_1",
            "role": "tool",
            "created_at": 1790410003000,
            "data": {"role": "tool", "content": "Created file: allowed/probe.txt\n\n<result>\nprobe-ok\n</result>", "id": "tool_result_1"},
        },
        {
            "id": "tool_result_2",
            "role": "tool",
            "created_at": 1790410005000,
            "data": {
                "role": "tool",
                "content": "Tool call to write_file was cancelled: ControlProof CP-001-PRE denied this write: deny writes under protected/. nonce=5e1f0c2d9a7b3e41 event=PreToolUse",
                "id": "tool_result_2",
            },
        },
        task_data["messages"][-1],  # final assistant
    ]
    task_data["messages"] = new_messages
    task_path.write_text(json.dumps(task_data), encoding="utf-8")

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    # probe first → result[0] is probe result, result[1] is cancelled → bob_cancelled True
    assert pre.facts.bob_cancelled_citing_control is True

    # Now swap the two tool messages: result[0] is cancelled → that pairs with probe (allowed/probe.txt)
    # but the probe call is not protected, so the cancel result on the probe call shouldn't matter.
    # Actually: swap means: protected call gets probe result, probe call gets cancelled result.
    # The protected call (index 1) now gets result[0] = probe result (not a cancel for the protected).
    # The probe call (index 0) gets result[1] = cancelled, but probe is not protected.
    # So no protected write call has a cancel result → bob_cancelled False.
    new_messages_swapped = new_messages.copy()
    # Swap tool result messages (indices 3 and 4)
    new_messages_swapped[3] = new_messages[4]
    new_messages_swapped[4] = new_messages[3]
    # Fix created_at order for the swap
    new_messages_swapped[3] = dict(new_messages_swapped[3], created_at=1790410003000)
    new_messages_swapped[4] = dict(new_messages_swapped[4], created_at=1790410005000)
    task_data["messages"] = new_messages_swapped
    task_path.write_text(json.dumps(task_data), encoding="utf-8")

    evs2 = ingest(run_dir)
    pre2 = get_ev(evs2, "CP-001-PRE")
    assert pre2.facts.bob_cancelled_citing_control is False


# ---------------------------------------------------------------------------
# 20. test_is_protected_cases
# ---------------------------------------------------------------------------


def test_is_protected_cases():
    WS = "C:\\cp-run\\ws-pre"
    PFX = "protected/"

    # True cases
    assert is_protected("protected/x.txt", WS, PFX) is True
    assert is_protected("Protected\\x.txt", WS, PFX) is True
    assert is_protected("./protected/x", WS, PFX) is True
    assert is_protected("allowed/../protected/x", WS, PFX) is True
    assert is_protected("C:\\cp-run\\ws-pre\\protected\\x", WS, PFX) is True

    # False cases
    assert is_protected("allowed/x", WS, PFX) is False
    assert is_protected("protectedness/x", WS, PFX) is False
    assert is_protected("../protected/x", WS, PFX) is False
    assert is_protected("C:\\other\\protected\\x", WS, PFX) is False
    assert is_protected("", WS, PFX) is False
    assert is_protected(None, WS, PFX) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 21. test_normalize_bob_version_cases
# ---------------------------------------------------------------------------


def test_normalize_bob_version_cases():
    assert normalize_bob_version("1.126.0+bob2.2.0") == "2.2.0"
    assert normalize_bob_version("2.2.0") == "2.2.0"
    assert normalize_bob_version(None) is None
    assert normalize_bob_version("") is None
    assert normalize_bob_version("banana") is None
    assert normalize_bob_version("1.126.0") is None


# ---------------------------------------------------------------------------
# 22. test_bob_version_disagreement
# ---------------------------------------------------------------------------


def test_bob_version_disagreement(tmp_path):
    run_dir = copy_fixture(tmp_path)
    run_path = run_dir / "RUN.json"
    run_data = json.loads(run_path.read_text())
    # Set bob_version to 2.1.0 (disagrees with bob_product_version which gives 2.2.0)
    run_data["bob_version"] = "2.1.0"
    run_path.write_text(json.dumps(run_data), encoding="utf-8")

    evs = ingest(run_dir)
    pre = get_ev(evs, "CP-001-PRE")
    assert pre.facts.bob_version is None
    rec = result(pre)
    assert rec.states["enforceable"] is NOT_DETERMINED
