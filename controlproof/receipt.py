"""ControlProof receipt — B4.

Build a JSON receipt and a plain-text summary from an evidence bundle.
Python 3.12, standard library only.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from controlproof.bundle import verify_bundle
from controlproof.conflicts import CONFLICT_MEANINGS, assess
from controlproof.ingest import (
    WRITE_TOOLS,
    is_protected,
    parse_cancellation,
)
from controlproof.model import (
    evaluate,
    state_symbol,
    RESULT_LABELS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_run(bundle_dir: Path) -> dict:
    return json.loads((bundle_dir / "RUN.json").read_bytes())


def _bob_cancellation_text(ev, workspace_path: str, protected_prefix: str) -> "str | None":
    """Return the tool result text of the first protected write call that was cancelled."""
    if ev.bob_tool_calls is None:
        return None
    for tc in ev.bob_tool_calls:
        if tc.name not in WRITE_TOOLS:
            continue
        if not is_protected(tc.arguments.get("path"), workspace_path, protected_prefix):
            continue
        if tc.result is not None:
            parsed = parse_cancellation(tc.result)
            if parsed is not None:
                return tc.result
    return None


def _protected_files_after(snapshot_files: "list[str] | None", workspace_path: str, protected_prefix: str) -> "list[str]":
    """Return the subset of snapshot files that are under the protected prefix."""
    if snapshot_files is None:
        return []
    result = []
    for f in snapshot_files:
        if is_protected(f, workspace_path, protected_prefix):
            result.append(f)
    return result


# ---------------------------------------------------------------------------
# build_receipt
# ---------------------------------------------------------------------------


def build_receipt(
    bundle_dir: "str | Path",
    pinned_manifest_sha256: "str | None" = None,
) -> dict:
    """Build a receipt dict from *bundle_dir*."""
    bundle_dir = Path(bundle_dir)
    check = verify_bundle(bundle_dir, pinned_manifest_sha256)
    evs = assess(bundle_dir)
    run = _load_run(bundle_dir)
    policy = run.get("policy", {})
    protected_prefix = policy.get("protected_prefix", "protected/")

    controls_out = []
    run_controls = run.get("controls", [])
    for i, ev in enumerate(evs):
        # Replace bundle_verified with the check result
        new_facts = dataclasses.replace(ev.facts, bundle_verified=check.ok)
        record = evaluate(new_facts)

        # Get the matching run control entry for title/workspace
        if i < len(run_controls):
            run_ctrl = run_controls[i]
        else:
            run_ctrl = {}
        title = run_ctrl.get("title") or ev.facts.control_id
        workspace = run_ctrl.get("workspace", "")
        workspace_path = run_ctrl.get("workspace_path", "")

        # bob_cancellation_text
        bob_cancel = _bob_cancellation_text(ev, workspace_path, protected_prefix)

        # protected_files_after from snapshot
        from controlproof.ingest import load_snapshot
        snapshot = load_snapshot(bundle_dir, workspace)
        if snapshot is not None and isinstance(snapshot.get("files"), list):
            pf_after = _protected_files_after(snapshot["files"], workspace_path, protected_prefix)
        else:
            pf_after = []

        # conflict meanings
        conflict_meanings = {
            c.code: CONFLICT_MEANINGS.get(c.code, c.code)
            for c in ev.facts.conflicts
        }

        # excluded_rows
        excluded_rows = dict(ev.excluded_rows)

        evidence = {
            "session_bound": ev.session_bound,
            "ledger_rows": ev.facts.ledger_rows,
            "excluded_rows": excluded_rows,
            "bob_cancellation_text": bob_cancel,
            "protected_files_after": pf_after,
            "conflict_meanings": conflict_meanings,
            "notes": list(ev.notes),
        }

        ctrl_out = dict(record.to_dict())
        ctrl_out["title"] = title
        ctrl_out["workspace"] = workspace
        ctrl_out["evidence"] = evidence
        controls_out.append(ctrl_out)

    receipt = {
        "schema": "controlproof.receipt/1",
        "bundle": {
            "verified": check.ok,
            "problems": list(check.problems),
            "manifest_sha256": check.manifest_sha256,
        },
        "run": {
            "run_id": run.get("run_id"),
            "bob_version": run.get("bob_version"),
            "policy_text": policy.get("text"),
            "prompt": run.get("prompt"),
        },
        "controls": controls_out,
    }
    return receipt


# ---------------------------------------------------------------------------
# receipt_text
# ---------------------------------------------------------------------------

_LABELS = ("Configured", "Executed", "Observed", "Can enforce", "Enforced")
_STATE_KEYS = ("configured", "executed", "observed", "enforceable", "enforced")


def receipt_text(receipt: dict) -> str:
    """Return a plain-text summary of the receipt."""
    lines: list[str] = []

    bundle = receipt.get("bundle", {})
    if bundle.get("verified"):
        lines.append("Bundle: Evidence bundle matches its recorded manifest.")
    else:
        lines.append("Bundle: EVIDENCE NOT VERIFIED — the bundle does not match its recorded manifest.")
        for p in bundle.get("problems", []):
            lines.append(f"  Problem: {p}")

    for ctrl in receipt.get("controls", []):
        title = ctrl.get("title", ctrl.get("control_id", ""))
        lines.append(f"\n{title}")
        states = ctrl.get("states", {})
        for label, key in zip(_LABELS, _STATE_KEYS):
            sym = state_symbol_from_json(states.get(key))
            lines.append(f"  {label}: {sym}")
        label = ctrl.get("label", "")
        reason = ctrl.get("reason", "")
        lines.append(f"  Result: {label}")
        lines.append(f"  Reason: {reason}")

    return "\n".join(lines)


def state_symbol_from_json(val: object) -> str:
    """Convert a JSON state value (True/False/'not_determined') to a symbol."""
    if val is True:
        return "\u2713"
    if val is False:
        return "\u2715"
    return "\u2014"
