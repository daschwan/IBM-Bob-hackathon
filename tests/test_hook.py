"""Tests for hook/controlproof_hook.py — task B3."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from controlproof.ingest import is_protected

HOOK_PATH = Path(__file__).parent.parent / "hook" / "controlproof_hook.py"

CONTROL_ID = "CP-TEST-001"
NONCE = "aabbccdd11223344"
SESSION_ID = "test-session-id-abc123"


def hook_sha256() -> str:
    h = hashlib.sha256()
    with open(HOOK_PATH, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_hook(
    tmp_path: Path,
    payload: dict,
    *,
    control_id: str = CONTROL_ID,
    nonce: str = NONCE,
    protected_prefix: str = "protected/",
    ledger_name: str = "ledger.jsonl",
) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the hook with payload on stdin, return (result, ledger_path)."""
    ledger = tmp_path / ledger_name
    cmd = [
        sys.executable,
        str(HOOK_PATH),
        control_id,
        "--ledger", str(ledger),
        "--nonce", nonce,
        "--protected-prefix", protected_prefix,
    ]
    result = subprocess.run(
        cmd,
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
    )
    return result, ledger


def read_rows(ledger: Path) -> list[dict]:
    if not ledger.exists():
        return []
    rows = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# 1. test_pretooluse_protected_write_denied
# ---------------------------------------------------------------------------


def test_pretooluse_protected_write_denied(tmp_path):
    payload = {
        "session_id": SESSION_ID,
        "cwd": "/workspace",
        "hook_event_name": "PreToolUse",
        "tool_name": "write_file",
        "tool_use_id": "tooluse_abc123",
        "tool_input": {"path": "protected/test.txt", "content": "bad"},
    }
    result, ledger = run_hook(tmp_path, payload)

    assert result.returncode == 2
    stderr = result.stderr.decode("utf-8").strip()
    expected_stderr = (
        f"ControlProof {CONTROL_ID} denied this write: deny writes under protected/."
        f" nonce={NONCE} event=PreToolUse"
    )
    assert stderr == expected_stderr

    rows = read_rows(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row["schema"] == "controlproof.ledger/1"
    assert row["control_id"] == CONTROL_ID
    assert row["run_nonce"] == NONCE
    assert row["session_id"] == SESSION_ID
    assert row["hook_event_name"] == "PreToolUse"
    assert row["tool_name"] == "write_file"
    assert row["tool_use_id"] == "tooluse_abc123"
    assert row["tool_input_path"] == "protected/test.txt"
    assert row["decision"] == "DENY"
    assert row["policy_triggered"] is True
    assert row["has_tool_response"] is False
    assert row["exit_code"] == 2
    assert row["hook_name"] == "controlproof-hook"
    assert row["hook_version"] == "1.0.0"
    assert row["hook_sha256"] == hook_sha256()
    # hook_invoked_at: UTC timestamp YYYY-MM-DDTHH:MM:SS.mmmZ
    assert row["hook_invoked_at"].endswith("Z")
    assert "T" in row["hook_invoked_at"]


# ---------------------------------------------------------------------------
# 2. test_allowed_write_passes
# ---------------------------------------------------------------------------


def test_allowed_write_passes(tmp_path):
    payload = {
        "session_id": SESSION_ID,
        "cwd": "/workspace",
        "hook_event_name": "PreToolUse",
        "tool_name": "write_file",
        "tool_use_id": "tooluse_allow001",
        "tool_input": {"path": "allowed/probe.txt", "content": "ok"},
    }
    result, ledger = run_hook(tmp_path, payload)

    assert result.returncode == 0
    assert result.stderr == b""

    rows = read_rows(ledger)
    assert len(rows) == 1
    assert rows[0]["decision"] == "ALLOW"
    assert rows[0]["tool_input_path"] == "allowed/probe.txt"


# ---------------------------------------------------------------------------
# 3. test_posttooluse_same_behaviour
# ---------------------------------------------------------------------------


def test_posttooluse_same_behaviour(tmp_path):
    payload = {
        "session_id": SESSION_ID,
        "cwd": "/workspace",
        "hook_event_name": "PostToolUse",
        "tool_name": "write_file",
        "tool_use_id": "tooluse_post001",
        "tool_input": {"path": "protected/test.txt", "content": "bad"},
        "tool_response": "Created file.",
    }
    result, ledger = run_hook(tmp_path, payload)

    assert result.returncode == 2
    rows = read_rows(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row["has_tool_response"] is True
    assert row["decision"] == "DENY"
    assert row["hook_event_name"] == "PostToolUse"


# ---------------------------------------------------------------------------
# 4. test_non_write_tool_not_triggered
# ---------------------------------------------------------------------------


def test_non_write_tool_not_triggered(tmp_path):
    payload = {
        "session_id": SESSION_ID,
        "cwd": "/workspace",
        "hook_event_name": "PreToolUse",
        "tool_name": "read_file",
        "tool_use_id": "tooluse_read001",
        "tool_input": {"path": "protected/x"},
    }
    result, ledger = run_hook(tmp_path, payload)

    assert result.returncode == 0
    assert result.stderr == b""
    rows = read_rows(ledger)
    assert len(rows) == 1
    assert rows[0]["decision"] == "ALLOW"
    assert rows[0]["policy_triggered"] is False


# ---------------------------------------------------------------------------
# 5. test_absolute_path_under_cwd
# ---------------------------------------------------------------------------


def test_absolute_path_under_cwd(tmp_path):
    cwd = "C:\\cp-run\\ws-test"
    payload = {
        "session_id": SESSION_ID,
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": "write_file",
        "tool_use_id": "tooluse_abs001",
        "tool_input": {"path": f"{cwd}\\protected\\sensitive.txt"},
    }
    result, ledger = run_hook(tmp_path, payload)

    assert result.returncode == 2
    rows = read_rows(ledger)
    assert rows[0]["decision"] == "DENY"


# ---------------------------------------------------------------------------
# 6. test_malformed_stdin
# ---------------------------------------------------------------------------


def test_malformed_stdin(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    cmd = [
        sys.executable,
        str(HOOK_PATH),
        CONTROL_ID,
        "--ledger", str(ledger),
        "--nonce", NONCE,
    ]
    result = subprocess.run(cmd, input=b"not json", capture_output=True)

    assert result.returncode == 1
    rows = read_rows(ledger)
    assert len(rows) == 1
    assert rows[0]["decision"] == "ERROR"


# ---------------------------------------------------------------------------
# 7. test_rows_append
# ---------------------------------------------------------------------------


def test_rows_append(tmp_path):
    payload1 = {
        "session_id": SESSION_ID,
        "cwd": "/workspace",
        "hook_event_name": "PreToolUse",
        "tool_name": "write_file",
        "tool_use_id": "tooluse_first",
        "tool_input": {"path": "allowed/a.txt"},
    }
    payload2 = {
        "session_id": SESSION_ID,
        "cwd": "/workspace",
        "hook_event_name": "PreToolUse",
        "tool_name": "write_file",
        "tool_use_id": "tooluse_second",
        "tool_input": {"path": "protected/b.txt"},
    }
    ledger = tmp_path / "ledger.jsonl"
    cmd_base = [
        sys.executable, str(HOOK_PATH), CONTROL_ID,
        "--ledger", str(ledger), "--nonce", NONCE,
    ]
    subprocess.run(cmd_base, input=json.dumps(payload1).encode(), capture_output=True)
    subprocess.run(cmd_base, input=json.dumps(payload2).encode(), capture_output=True)

    rows = read_rows(ledger)
    assert len(rows) == 2
    assert rows[0]["tool_use_id"] == "tooluse_first"
    assert rows[0]["decision"] == "ALLOW"
    assert rows[1]["tool_use_id"] == "tooluse_second"
    assert rows[1]["decision"] == "DENY"


# ---------------------------------------------------------------------------
# 8. test_hook_matches_ingest_normalization
# ---------------------------------------------------------------------------


def test_hook_matches_ingest_normalization(tmp_path):
    """Hook's is_protected agrees with controlproof.ingest.is_protected on B2 section-2 cases."""
    spec = importlib.util.spec_from_file_location("controlproof_hook", HOOK_PATH)
    hook_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook_module)
    hook_is_protected = hook_module.is_protected

    WS = "C:\\cp-run\\ws-pre"
    PFX = "protected/"

    test_cases = [
        # (path, expected_result)
        ("protected/x.txt", True),
        ("Protected\\x.txt", True),
        ("./protected/x", True),
        ("allowed/../protected/x", True),
        ("C:\\cp-run\\ws-pre\\protected\\x", True),
        ("allowed/x", False),
        ("protectedness/x", False),
        ("../protected/x", False),
        ("C:\\other\\protected\\x", False),
        ("", False),
        (None, False),
    ]
    for path, expected in test_cases:
        hook_result = hook_is_protected(path, WS, PFX)
        ingest_result = is_protected(path, WS, PFX)
        assert hook_result == expected, f"hook: is_protected({path!r}, ...) = {hook_result!r}, expected {expected!r}"
        assert ingest_result == expected, f"ingest: is_protected({path!r}, ...) = {ingest_result!r}, expected {expected!r}"
        assert hook_result == ingest_result, f"hook and ingest disagree on {path!r}"


# ---------------------------------------------------------------------------
# 9. test_hook_is_standalone
# ---------------------------------------------------------------------------


def test_hook_is_standalone():
    """The hook source imports nothing from controlproof."""
    source = HOOK_PATH.read_text(encoding="utf-8")
    # Check there's no import of controlproof
    assert "from controlproof" not in source
    assert "import controlproof" not in source
