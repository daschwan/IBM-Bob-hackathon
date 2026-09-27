#!/usr/bin/env python3
"""ControlProof hook — standalone, standard library only, no controlproof imports.

Usage:
    controlproof_hook.py <CONTROL_ID> --ledger <path> --nonce <nonce>
                         [--protected-prefix protected/]

Reads JSON from stdin (hook payload), appends one ledger row, exits 0 (ALLOW)
or 2 (DENY).  Exit 1 on argument/parse errors.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import sys
from datetime import datetime, timezone

HOOK_NAME = "controlproof-hook"
HOOK_VERSION = "1.0.0"
WRITE_TOOLS = frozenset({"write_file", "apply_diff", "insert_content", "search_and_replace"})


# ---------------------------------------------------------------------------
# Path helpers (re-implementation of controlproof.ingest.is_protected)
# ---------------------------------------------------------------------------


def _norm_path(p: str | None) -> str:
    if not p:
        return ""
    p = p.replace("\\", "/")
    while "//" in p:
        p = p.replace("//", "/")
    return p.lower()


def is_protected(
    path: str | None,
    workspace_path: str | None,
    protected_prefix: str | None,
) -> bool:
    """Return True iff *path* refers to a file under *protected_prefix* in *workspace_path*."""
    if not isinstance(path, str) or not path:
        return False

    norm_path = _norm_path(path)
    norm_ws = _norm_path(workspace_path).rstrip("/")
    norm_prefix = _norm_path(protected_prefix)

    is_absolute = bool(re.match(r"[a-z]:/", norm_path)) or norm_path.startswith("/")
    if is_absolute:
        if norm_ws and norm_path.startswith(norm_ws + "/"):
            norm_path = norm_path[len(norm_ws) + 1:]
        else:
            return False

    norm_path = posixpath.normpath(norm_path)
    if norm_path == ".." or norm_path.startswith("../"):
        return False

    norm_prefix = norm_prefix.rstrip("/")
    return norm_path == norm_prefix or norm_path.startswith(norm_prefix + "/")


# ---------------------------------------------------------------------------
# SHA-256 of the hook file itself
# ---------------------------------------------------------------------------


def _hook_sha256() -> str:
    path = os.path.abspath(__file__)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Ledger append
# ---------------------------------------------------------------------------


def _append_ledger(ledger_path: str, row: dict) -> None:
    line = json.dumps(row, separators=(",", ":")) + "\n"
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("control_id", nargs="?")
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--nonce", default=None)
    parser.add_argument("--protected-prefix", default="protected/")

    args, _ = parser.parse_known_args()

    # Validate required arguments
    if not args.control_id or not args.ledger or not args.nonce:
        print(
            "Usage: controlproof_hook.py <CONTROL_ID> --ledger <path> --nonce <nonce>",
            file=sys.stderr,
        )
        sys.exit(1)

    control_id: str = args.control_id
    ledger_path: str = args.ledger
    run_nonce: str = args.nonce
    protected_prefix: str = args.protected_prefix

    hook_sha = _hook_sha256()
    _now = datetime.now(timezone.utc)
    hook_invoked_at = _now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{_now.microsecond // 1000:03d}Z"

    # Read stdin
    raw_stdin = sys.stdin.buffer.read()
    payload: dict | None = None
    parse_error = False
    try:
        obj = json.loads(raw_stdin.decode("utf-8"))
        if isinstance(obj, dict):
            payload = obj
        else:
            parse_error = True
    except (json.JSONDecodeError, UnicodeDecodeError):
        parse_error = True

    if parse_error or payload is None:
        row = {
            "schema": "controlproof.ledger/1",
            "control_id": control_id,
            "run_nonce": run_nonce,
            "session_id": None,
            "hook_event_name": None,
            "tool_name": None,
            "tool_use_id": None,
            "tool_input_path": None,
            "decision": "ERROR",
            "policy_triggered": None,
            "has_tool_response": None,
            "exit_code": 1,
            "hook_name": HOOK_NAME,
            "hook_version": HOOK_VERSION,
            "hook_sha256": hook_sha,
            "hook_invoked_at": hook_invoked_at,
        }
        _append_ledger(ledger_path, row)
        sys.exit(1)

    session_id = payload.get("session_id")
    hook_event_name = payload.get("hook_event_name")
    tool_name = payload.get("tool_name")
    tool_use_id = payload.get("tool_use_id")
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd")

    tool_input_path = tool_input.get("path") if isinstance(tool_input.get("path"), str) else None
    has_tool_response = "tool_response" in payload

    policy_triggered = (
        tool_name in WRITE_TOOLS
        and is_protected(tool_input_path, cwd, protected_prefix)
    )
    decision = "DENY" if policy_triggered else "ALLOW"
    exit_code = 2 if policy_triggered else 0

    row = {
        "schema": "controlproof.ledger/1",
        "control_id": control_id,
        "run_nonce": run_nonce,
        "session_id": session_id,
        "hook_event_name": hook_event_name,
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "tool_input_path": tool_input_path,
        "decision": decision,
        "policy_triggered": policy_triggered,
        "has_tool_response": has_tool_response,
        "exit_code": exit_code,
        "hook_name": HOOK_NAME,
        "hook_version": HOOK_VERSION,
        "hook_sha256": hook_sha,
        "hook_invoked_at": hook_invoked_at,
    }
    _append_ledger(ledger_path, row)

    if policy_triggered:
        print(
            f"ControlProof {control_id} denied this write: deny writes under {protected_prefix}."
            f" nonce={run_nonce} event={hook_event_name}",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
