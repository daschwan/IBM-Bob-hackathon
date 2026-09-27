"""tests/test_final_build.py — acceptance tests for B4.

Tests for bundle verification, receipt, judge page, capture, provision, and CLI.
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "run-3arm"
HOOK_SRC = REPO_ROOT / "hook" / "controlproof_hook.py"


# ---------------------------------------------------------------------------
# Helper: compute sha256 of a file
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# make_bundle helper
# ---------------------------------------------------------------------------


def make_bundle(tmp_path: Path) -> Path:
    """Copy run-3arm fixture, wire the real hook, and write a fresh MANIFEST.json."""
    from controlproof.bundle import write_manifest

    bundle_dir = tmp_path / "bundle"
    shutil.copytree(FIXTURE_DIR, bundle_dir)

    # Copy the real hook
    hook_dest = bundle_dir / "hook" / "controlproof_hook.py"
    hook_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HOOK_SRC, hook_dest)

    real_sha = _sha256_file(HOOK_SRC)

    # Inject titles into RUN.json controls from the CONTROLS definition
    from controlproof.capture import CONTROLS as CTRL_DEFS
    run_path = bundle_dir / "RUN.json"
    run = json.loads(run_path.read_bytes())
    ctrl_titles = {c["control_id"]: c["title"] for c in CTRL_DEFS}
    for ctrl in run.get("controls", []):
        cid = ctrl.get("control_id", "")
        if cid in ctrl_titles:
            ctrl["title"] = ctrl_titles[cid]
    run_path.write_bytes(
        (json.dumps(run, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )

    # Update every ledger row's hook_sha256 to the real hook sha
    ledger_path = bundle_dir / "ledger.jsonl"
    updated_rows = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        row["hook_sha256"] = real_sha
        updated_rows.append(json.dumps(row, separators=(",", ":")))
    ledger_path.write_text("\n".join(updated_rows) + "\n", encoding="utf-8")

    # Update RUN.json hook section
    run_path = bundle_dir / "RUN.json"
    run = json.loads(run_path.read_bytes())
    run["hook"] = {
        "path": "hook/controlproof_hook.py",
        "sha256": real_sha,
        "name": "controlproof-hook",
        "version": "1.0.0",
    }
    run_path.write_bytes(
        (json.dumps(run, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )

    write_manifest(bundle_dir)
    return bundle_dir


# ---------------------------------------------------------------------------
# Test 1: clean bundle verifies and results are correct
# ---------------------------------------------------------------------------


def test_clean_bundle_verifies_and_receipt_results(tmp_path):
    from controlproof.bundle import verify_bundle
    from controlproof.receipt import build_receipt
    from controlproof.model import (
        CONTROL_ENFORCEMENT_VERIFIED,
        CONTROL_OBSERVATIONAL_ONLY,
        CONTROL_CONFIGURED_NOT_EXECUTED,
    )

    bundle_dir = make_bundle(tmp_path)
    check = verify_bundle(bundle_dir)
    assert check.ok, f"Expected ok but got problems: {check.problems}"

    receipt = build_receipt(bundle_dir)
    results = [c["result"] for c in receipt["controls"]]
    assert results[0] == CONTROL_ENFORCEMENT_VERIFIED
    assert results[1] == CONTROL_OBSERVATIONAL_ONLY
    assert results[2] == CONTROL_CONFIGURED_NOT_EXECUTED


# ---------------------------------------------------------------------------
# Test 2: altered file suppresses all verdicts
# ---------------------------------------------------------------------------


def test_altered_file_suppresses_all_verdicts(tmp_path):
    from controlproof.bundle import verify_bundle
    from controlproof.receipt import build_receipt
    from controlproof.model import EVIDENCE_NOT_VERIFIED

    bundle_dir = make_bundle(tmp_path)

    # Change one byte in ledger.jsonl (no re-manifest)
    ledger_path = bundle_dir / "ledger.jsonl"
    data = ledger_path.read_bytes()
    ledger_path.write_bytes(data[:-1] + b"X")

    check = verify_bundle(bundle_dir)
    assert not check.ok
    # At least one problem should name the file
    assert any("ledger.jsonl" in p for p in check.problems), check.problems

    receipt = build_receipt(bundle_dir)
    for ctrl in receipt["controls"]:
        assert ctrl["result"] == EVIDENCE_NOT_VERIFIED, ctrl["result"]
        assert not ctrl["confident"]

    from controlproof.render import render_html
    page = render_html(receipt)
    assert "EVIDENCE NOT VERIFIED" in page
    assert "ENFORCEMENT VERIFIED" not in page


# ---------------------------------------------------------------------------
# Test 3: added or removed file fails
# ---------------------------------------------------------------------------


def test_added_or_removed_file_fails(tmp_path):
    from controlproof.bundle import verify_bundle

    # Extra file
    bundle_dir = make_bundle(tmp_path)
    extra = bundle_dir / "extra.txt"
    extra.write_text("extra", encoding="utf-8")
    check = verify_bundle(bundle_dir)
    assert not check.ok
    assert any("extra.txt" in p for p in check.problems), check.problems

    # Deleted listed file
    bundle_dir2 = make_bundle(tmp_path / "b2")
    shutil.copytree(FIXTURE_DIR, bundle_dir2 / "src")  # dummy copy so tmp_path/b2 exists
    bundle_dir2 = make_bundle(tmp_path / "b2x")
    # Remove the ledger.jsonl (it's listed)
    (bundle_dir2 / "ledger.jsonl").unlink()
    check2 = verify_bundle(bundle_dir2)
    assert not check2.ok
    assert any("ledger.jsonl" in p for p in check2.problems), check2.problems


# ---------------------------------------------------------------------------
# Test 4: pin catches rewritten bundle
# ---------------------------------------------------------------------------


def test_pin_catches_rewritten_bundle(tmp_path):
    from controlproof.bundle import verify_bundle, write_manifest

    bundle_dir = make_bundle(tmp_path)
    # Record the original manifest sha
    original_sha = verify_bundle(bundle_dir).manifest_sha256

    # Rewrite a PRE ledger row and re-manifest
    ledger_path = bundle_dir / "ledger.jsonl"
    rows = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("control_id") == "CP-001-PRE" and row.get("decision") == "DENY":
            row["decision"] = "ALLOW"
        rows.append(json.dumps(row, separators=(",", ":")))
    ledger_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    write_manifest(bundle_dir)

    # Without pin: verify is ok
    check_no_pin = verify_bundle(bundle_dir)
    assert check_no_pin.ok, check_no_pin.problems

    # With the original pin: verify fails
    check_with_pin = verify_bundle(bundle_dir, pinned_manifest_sha256=original_sha)
    assert not check_with_pin.ok
    assert any("pinned" in p.lower() or original_sha[:8] in p for p in check_with_pin.problems)


# ---------------------------------------------------------------------------
# Test 5: foreign session not supporting
# ---------------------------------------------------------------------------


def test_foreign_session_not_supporting(tmp_path):
    from controlproof.bundle import write_manifest
    from controlproof.receipt import build_receipt
    from controlproof.model import CONTROL_ENFORCEMENT_VERIFIED

    bundle_dir = make_bundle(tmp_path)

    # Set PRE's session_id to POST's session
    run_path = bundle_dir / "RUN.json"
    run = json.loads(run_path.read_bytes())
    post_session = run["controls"][1]["session_id"]
    run["controls"][0]["session_id"] = post_session
    run_path.write_bytes(
        (json.dumps(run, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )
    write_manifest(bundle_dir)

    receipt = build_receipt(bundle_dir)
    pre_result = receipt["controls"][0]["result"]
    assert pre_result != CONTROL_ENFORCEMENT_VERIFIED, (
        f"Expected PRE not to be ENFORCEMENT_VERIFIED, got {pre_result}"
    )


# ---------------------------------------------------------------------------
# Test 6: conflict shown as conflict
# ---------------------------------------------------------------------------


def test_conflict_shown_as_conflict(tmp_path):
    from controlproof.bundle import write_manifest
    from controlproof.receipt import build_receipt
    from controlproof.render import render_html
    from controlproof.model import CONTROL_CONFLICTING_EVIDENCE

    bundle_dir = make_bundle(tmp_path)

    # Add protected/test.txt to PRE's after-state snapshot
    snap_path = bundle_dir / "snapshots" / "ws-pre.AFTER.json"
    snap = json.loads(snap_path.read_bytes())
    snap["files"] = sorted(snap.get("files", []) + ["protected/test.txt"])
    snap_path.write_bytes(
        (json.dumps(snap, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )
    write_manifest(bundle_dir)

    receipt = build_receipt(bundle_dir)
    pre = receipt["controls"][0]
    assert pre["result"] == CONTROL_CONFLICTING_EVIDENCE, pre["result"]

    page = render_html(receipt)
    assert "CONFLICTING EVIDENCE" in page
    assert "CANCELLED_BUT_TARGET_EXISTS" in page


# ---------------------------------------------------------------------------
# Test 7: receipt derives from records
# ---------------------------------------------------------------------------


def test_receipt_derives_from_records(tmp_path):
    from controlproof.bundle import verify_bundle
    from controlproof.conflicts import assess
    from controlproof.receipt import build_receipt
    from controlproof.model import evaluate

    bundle_dir = make_bundle(tmp_path)
    check = verify_bundle(bundle_dir)

    evs = assess(bundle_dir)
    receipt = build_receipt(bundle_dir)

    for i, ev in enumerate(evs):
        new_facts = dataclasses.replace(ev.facts, bundle_verified=check.ok)
        record = evaluate(new_facts)
        expected = record.to_dict()
        ctrl = receipt["controls"][i]
        for key in expected:
            assert ctrl[key] == expected[key], (
                f"Control {i} key {key!r}: "
                f"expected {expected[key]!r}, got {ctrl[key]!r}"
            )


# ---------------------------------------------------------------------------
# Test 8: page derives from receipt
# ---------------------------------------------------------------------------


def test_page_derives_from_receipt(tmp_path):
    from controlproof.receipt import build_receipt
    from controlproof.render import render_html
    from controlproof.model import RESULT_LABELS

    bundle_dir = make_bundle(tmp_path)
    receipt = build_receipt(bundle_dir)

    # Swap the first two controls' result, label, tone, states
    ctrls = list(receipt["controls"])
    first = dict(ctrls[0])
    second = dict(ctrls[1])

    first["result"], second["result"] = second["result"], first["result"]
    first["label"], second["label"] = second["label"], first["label"]
    first["tone"], second["tone"] = second["tone"], first["tone"]
    first["states"], second["states"] = second["states"], first["states"]
    ctrls[0] = first
    ctrls[1] = second
    receipt = dict(receipt)
    receipt["controls"] = ctrls

    page = render_html(receipt)

    # First card (index 0) now has POST's result which is OBSERVATIONAL ONLY
    assert "OBSERVATIONAL ONLY" in page
    # OBSERVATIONAL has enforceable=False (✕ for Can enforce)
    # The page should show ✕ for Can enforce somewhere
    assert "\u2715" in page  # ✕


# ---------------------------------------------------------------------------
# Test 9: clean page content
# ---------------------------------------------------------------------------


def test_clean_page_content(tmp_path):
    from controlproof.receipt import build_receipt
    from controlproof.render import render_html

    bundle_dir = make_bundle(tmp_path)
    receipt = build_receipt(bundle_dir)
    page = render_html(receipt)

    # Required elements
    assert "CONTROLPROOF" in page
    assert "Same policy. Same action. Three different realities." in page

    # Three titles
    assert "PRETOOLUSE" in page
    assert "POSTTOOLUSE" in page
    assert "BROKEN MATCHER" in page

    # "View Evidence" three times
    assert page.count("View Evidence") == 3

    # "Can enforce" appears in all three cards
    assert page.count("Can enforce") == 3

    # Card 1 (PRE): ENFORCEMENT_VERIFIED → all five ✓ (configured✓ executed✓ observed✓ enforceable✓ enforced✓)
    # Card 2 (POST): OBSERVATIONAL_ONLY → configured✓ executed✓ observed✓ enforceable✕ enforced✕
    # Card 3 (BADCFG): CONFIGURED_NOT_EXECUTED → configured✓ executed✕ observed✕ enforceable— enforced✕

    # We check that the labels appear in the page
    assert "ENFORCEMENT VERIFIED" in page
    assert "OBSERVATIONAL ONLY" in page
    assert "CONFIGURED" in page  # CONFIGURED — NOT EXECUTED


# ---------------------------------------------------------------------------
# Test 10: hook digest mismatch visible
# ---------------------------------------------------------------------------


def test_hook_digest_mismatch_visible(tmp_path):
    from controlproof.bundle import verify_bundle, write_manifest
    from controlproof.receipt import build_receipt
    from controlproof.model import CONTROL_CONFLICTING_EVIDENCE

    bundle_dir = make_bundle(tmp_path)

    # Part 1: change RUN.json hook.sha256 only (not re-hashing the file itself)
    run_path = bundle_dir / "RUN.json"
    run = json.loads(run_path.read_bytes())
    run["hook"]["sha256"] = "a" * 64
    run_path.write_bytes(
        (json.dumps(run, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )
    write_manifest(bundle_dir)

    check = verify_bundle(bundle_dir)
    assert not check.ok, "Expected verify to fail when hook sha mismatch"
    assert any("hook" in p.lower() for p in check.problems), check.problems

    # Part 2: change the bundled hook content and update RUN.json hook.sha256 to match,
    # but leave ledger rows at the original sha.
    # This makes the bundle verify OK, but ledger rows disagree with declared sha → HOOK_DIGEST_MISMATCH.
    bundle_dir2 = make_bundle(tmp_path / "b2")

    # Write a modified hook file (add a comment at the end) to get a different sha
    hook_path2 = bundle_dir2 / "hook" / "controlproof_hook.py"
    original_hook_content = hook_path2.read_bytes()
    modified_hook_content = original_hook_content + b"\n# modified\n"
    hook_path2.write_bytes(modified_hook_content)
    import hashlib
    new_hook_sha = hashlib.sha256(modified_hook_content).hexdigest()

    # Update RUN.json hook.sha256 to the new file sha (they agree)
    run_path2 = bundle_dir2 / "RUN.json"
    run2 = json.loads(run_path2.read_bytes())
    run2["hook"]["sha256"] = new_hook_sha
    run_path2.write_bytes(
        (json.dumps(run2, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )
    # Write manifest — bundle now verifies OK (hook file matches declared sha)
    write_manifest(bundle_dir2)

    # Verify the bundle is OK
    check2 = verify_bundle(bundle_dir2)
    assert check2.ok, f"Expected bundle2 to verify OK: {check2.problems}"

    receipt2 = build_receipt(bundle_dir2)
    # PRE and POST have bound ledger rows — they should get HOOK_DIGEST_MISMATCH conflict
    # (ledger rows still have original real_sha != new_hook_sha)
    for ctrl in receipt2["controls"][:2]:
        assert ctrl["result"] == CONTROL_CONFLICTING_EVIDENCE, (
            f"{ctrl['control_id']}: expected CONFLICTING_EVIDENCE, got {ctrl['result']}"
        )
        conflict_codes = [c["code"] for c in ctrl["conflicts"]]
        assert "HOOK_DIGEST_MISMATCH" in conflict_codes, conflict_codes


# ---------------------------------------------------------------------------
# Test 11: page escapes evidence text
# ---------------------------------------------------------------------------


def test_page_escapes_evidence_text(tmp_path):
    from controlproof.bundle import write_manifest
    from controlproof.receipt import build_receipt
    from controlproof.render import render_html

    bundle_dir = make_bundle(tmp_path)

    # Inject a script tag into PRE's cancellation result
    task_file = bundle_dir / "bob_tasks" / "3f9a1c7e5b2d4f60a8e1c3b5d7f90a12.json"
    task = json.loads(task_file.read_bytes())
    # Find the tool result message for the protected write
    for msg in task["messages"]:
        if msg.get("role") == "tool" and msg.get("id") == "3f9a1c7emsg05":
            original = msg["data"]["content"]
            # Replace the cancellation text with one containing a script tag
            msg["data"]["content"] = (
                "Tool call to write_file was cancelled: "
                "ControlProof CP-001-PRE denied this write: "
                "<script>alert(1)</script> "
                "nonce=5e1f0c2d9a7b3e41 event=PreToolUse"
            )
            break
    task_file.write_bytes(
        (json.dumps(task, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )
    write_manifest(bundle_dir)

    receipt = build_receipt(bundle_dir)
    page = render_html(receipt)

    assert "&lt;script&gt;" in page
    assert "<script" not in page


# ---------------------------------------------------------------------------
# Test 12: provision layout
# ---------------------------------------------------------------------------


def test_provision_layout(tmp_path):
    from controlproof.capture import provision, CONTROLS, DEMO_PROMPT

    run_dir = tmp_path / "run"
    draft = provision(run_dir, REPO_ROOT)

    # Check structure
    assert draft["schema"] == "controlproof.run/1"
    assert len(draft["run_nonce"]) == 16  # 8 bytes hex = 16 chars
    assert draft["prompt"] == DEMO_PROMPT
    assert len(draft["controls"]) == 3

    # Check each workspace
    for ctrl_def, ctrl_out in zip(CONTROLS, draft["controls"]):
        assert ctrl_out["control_id"] == ctrl_def["control_id"]
        assert ctrl_out["workspace"] == ctrl_def["workspace"]
        assert ctrl_out["title"] == ctrl_def["title"]
        assert "session_id" not in ctrl_out  # draft: no session ids

        ws_path = run_dir / ctrl_def["workspace"]
        assert (ws_path / "allowed").is_dir()
        assert (ws_path / "protected").is_dir()
        settings_path = ws_path / ".bob" / "settings.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_bytes())
        hooks_section = settings["hooks"]
        event = ctrl_def["event"]
        assert event in hooks_section
        entries = hooks_section[event]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["matcher"] == ctrl_def["matcher"]
        assert entry["hooks"][0]["timeout"] == 15
        cmd = entry["hooks"][0]["command"]
        assert ctrl_def["control_id"] in cmd
        assert "--ledger" in cmd
        assert "--nonce" in cmd
        assert draft["run_nonce"] in cmd

    # Second provision into same dir should refuse
    with pytest.raises((ValueError, Exception)):
        provision(run_dir, REPO_ROOT)


# ---------------------------------------------------------------------------
# Test 13: capture from fake bob db
# ---------------------------------------------------------------------------


def test_capture_from_fake_bob_db(tmp_path):
    from controlproof.capture import provision, capture, CONTROLS

    # Provision
    run_dir = tmp_path / "run"
    draft = provision(run_dir, REPO_ROOT)

    # Create fake ledger rows, files and log
    evidence_dir = run_dir / "evidence"
    nonce = draft["run_nonce"]

    # Create workspace files
    (run_dir / "ws-pre" / "allowed").mkdir(parents=True, exist_ok=True)
    (run_dir / "ws-pre" / "allowed" / "probe.txt").write_text("probe-ok", encoding="utf-8")
    (run_dir / "ws-post" / "allowed").mkdir(parents=True, exist_ok=True)
    (run_dir / "ws-post" / "allowed" / "probe.txt").write_text("probe-ok", encoding="utf-8")
    (run_dir / "ws-badcfg" / "allowed").mkdir(parents=True, exist_ok=True)
    (run_dir / "ws-badcfg" / "allowed" / "probe.txt").write_text("probe-ok", encoding="utf-8")

    # Write a fake log file
    boblogs_dir = run_dir / "boblogs"
    boblogs_dir.mkdir(parents=True, exist_ok=True)
    (boblogs_dir / "run.log").write_text("{\"ts\": \"2026-01-01T00:00:00Z\", \"level\": \"info\", \"msg\": \"start\"}\n", encoding="utf-8")

    # Write minimal ledger
    (evidence_dir / "ledger.jsonl").write_text("", encoding="utf-8")

    # Create SQLite db
    db_path = tmp_path / "bob.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, project_id TEXT, env TEXT, status TEXT, created_at INTEGER)"
    )
    conn.execute(
        "CREATE TABLE messages (id TEXT PRIMARY KEY, task_id TEXT, role TEXT, data TEXT, created_at INTEGER)"
    )

    # One task per workspace, created after provisioned_at_ms
    provisioned_at = draft["provisioned_at_ms"]
    ws_ids = {
        "ws-pre": "task-pre-001",
        "ws-post": "task-post-001",
        "ws-badcfg": "task-badcfg-001",
    }
    for ctrl in draft["controls"]:
        ws_name = ctrl["workspace"]
        ws_path = ctrl["workspace_path"]
        task_id = ws_ids[ws_name]
        env_json = json.dumps({"workspace": ws_path})
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
            (task_id, f"file:{ws_path}", env_json, "active", provisioned_at + 1000),
        )
        # Add one message
        msg_data = json.dumps({"role": "assistant", "content": "done", "toolCalls": []})
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
            (f"msg-{task_id}-1", task_id, "assistant", msg_data, provisioned_at + 2000),
        )
    conn.commit()
    conn.close()

    # Product json
    product_json_path = tmp_path / "product.json"
    product_json_path.write_text(
        json.dumps({"version": "1.126.0+bob2.2.0"}), encoding="utf-8"
    )

    # Capture
    bundle_dir = tmp_path / "bundle"
    manifest_sha = capture(
        run_dir=run_dir,
        bundle_dir=bundle_dir,
        repo_root=REPO_ROOT,
        bob_db=db_path,
        bob_product_json=product_json_path,
    )

    # Verify manifest
    from controlproof.bundle import verify_bundle
    check = verify_bundle(bundle_dir)
    assert check.ok, check.problems

    # Check RUN.json has session ids and bob_version
    run_out = json.loads((bundle_dir / "RUN.json").read_bytes())
    assert run_out["bob_version"] == "2.2.0"
    session_ids = {c["workspace"]: c["session_id"] for c in run_out["controls"]}
    assert session_ids["ws-pre"] == "task-pre-001"
    assert session_ids["ws-post"] == "task-post-001"
    assert session_ids["ws-badcfg"] == "task-badcfg-001"

    # Add a second task in ws-pre — capture should raise ValueError
    conn2 = sqlite3.connect(str(db_path))
    env_pre = json.dumps({"workspace": draft["controls"][0]["workspace_path"]})
    conn2.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
        ("task-pre-002", "file:extra", env_pre, "active", provisioned_at + 5000),
    )
    conn2.commit()
    conn2.close()

    bundle_dir2 = tmp_path / "bundle2"
    with pytest.raises(ValueError, match="ws-pre"):
        capture(
            run_dir=run_dir,
            bundle_dir=bundle_dir2,
            repo_root=REPO_ROOT,
            bob_db=db_path,
            bob_product_json=product_json_path,
        )

    # But passing sessions explicitly should work
    bundle_dir3 = tmp_path / "bundle3"
    capture(
        run_dir=run_dir,
        bundle_dir=bundle_dir3,
        repo_root=REPO_ROOT,
        bob_db=db_path,
        bob_product_json=product_json_path,
        sessions={"ws-pre": "task-pre-001", "ws-post": "task-post-001", "ws-badcfg": "task-badcfg-001"},
    )


# ---------------------------------------------------------------------------
# Test 14: CLI demo UTF-8
# ---------------------------------------------------------------------------


def test_cli_demo_utf8(tmp_path):
    bundle_dir = make_bundle(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "controlproof.py"),
         "demo", "--bundle", str(bundle_dir), "--out", str(out_dir), "--no-pin"],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Exit code {result.returncode}\nSTDOUT: {result.stdout!r}\nSTDERR: {result.stderr!r}"
    )

    # Three files exist
    assert (out_dir / "index.html").exists()
    assert (out_dir / "receipt.json").exists()
    assert (out_dir / "receipt.txt").exists()

    # stdout decodes as UTF-8 and contains ✓
    stdout = result.stdout.decode("utf-8")
    assert "\u2713" in stdout, f"No ✓ in stdout: {stdout[:200]!r}"


# ---------------------------------------------------------------------------
# Test 15: shipped hook matches pinned bundles
# ---------------------------------------------------------------------------


def test_shipped_hook_matches_pinned_bundles(tmp_path):
    from controlproof.bundle import load_pins

    real_sha = _sha256_file(HOOK_SRC)

    # For any bundle in PINS.json, check the bundled hook sha equals the repo hook
    pins = load_pins(REPO_ROOT)
    for rel_path, pinned_sha in pins.get("bundles", {}).items():
        bundle_path = REPO_ROOT / rel_path
        if not bundle_path.exists():
            continue
        bundled_hook = bundle_path / "hook" / "controlproof_hook.py"
        if bundled_hook.exists():
            bundled_sha = _sha256_file(bundled_hook)
            assert bundled_sha == real_sha, (
                f"Bundle {rel_path}: bundled hook sha {bundled_sha} != repo hook sha {real_sha}"
            )

    # For make_bundle, RUN.json hook.sha256 equals the repo hook's sha256
    bundle_dir = make_bundle(tmp_path)
    run = json.loads((bundle_dir / "RUN.json").read_bytes())
    assert run["hook"]["sha256"] == real_sha


# ---------------------------------------------------------------------------
# Test 16: strict cancellation prefix
# ---------------------------------------------------------------------------


def test_strict_cancellation_prefix():
    from controlproof.ingest import parse_cancellation

    # "failed:" is not "was cancelled:" — must return None
    bad = "Tool call to write_file failed: ControlProof CP-001-PRE denied this write"
    assert parse_cancellation(bad) is None

    # The real fixture cancellation should parse with tool_name "write_file"
    real = (
        "Tool call to write_file was cancelled: "
        "ControlProof CP-001-PRE denied this write: "
        "deny writes under protected/. nonce=5e1f0c2d9a7b3e41 event=PreToolUse"
    )
    result = parse_cancellation(real)
    assert result is not None
    assert result["tool_name"] == "write_file"
    assert result["control_id"] == "CP-001-PRE"
