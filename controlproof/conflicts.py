"""ControlProof conflict detection — B3.

`assess(evidence_dir)` runs ingest then detects conflicts for each control.
`detect_conflicts(ev, run, evidence_dir)` detects conflicts for one control.

Python 3.12, standard library only.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

from controlproof.ingest import (
    ControlEvidence,
    WRITE_TOOLS,
    bob_log_lines,
    cancellation_for_call,
    control_config,
    ingest,
    load_settings,
    parse_cancellation,
    workspace_relative,
)
from controlproof.model import (
    NOT_DETERMINED,
    Conflict,
)

# ---------------------------------------------------------------------------
# Conflict registry
# ---------------------------------------------------------------------------

CONFLICT_AFFECTS: dict[str, tuple[str, ...]] = {
    "LIFECYCLE_MISMATCH": ("enforceable", "enforced"),
    "MULTIPLE_EVENTS": ("enforceable", "enforced"),
    "LEDGER_PATH_MISMATCH": ("executed", "observed", "enforced"),
    "DUPLICATE_DISAGREES": ("executed", "observed", "enforced"),
    "CANCELLED_BUT_TARGET_EXISTS": ("enforced",),
    "CANCELLATION_IDENTITY_MISMATCH": ("enforced",),
    "HOOK_DIGEST_MISMATCH": ("executed", "observed", "enforceable", "enforced"),
    "LEDGER_CONTRADICTS_INVALID_MATCHER": ("executed", "observed", "enforceable", "enforced"),
    "NOT_CONFIGURED_BUT_RAN": ("configured",),
    "BOB_VERSION_MISMATCH": ("enforceable", "enforced"),
}

CONFLICT_MEANINGS: dict[str, str] = {
    "LIFECYCLE_MISMATCH": (
        "A bound ledger row's hook_event_name differs from the configured lifecycle event."
    ),
    "MULTIPLE_EVENTS": (
        "Bound ledger rows carry more than one distinct hook_event_name."
    ),
    "LEDGER_PATH_MISMATCH": (
        "A bound ledger row's tool_input_path disagrees with the path in Bob's own record."
    ),
    "DUPLICATE_DISAGREES": (
        "Two bound rows share a tool_use_id but differ in a load-bearing field."
    ),
    "CANCELLED_BUT_TARGET_EXISTS": (
        "Bob's record shows a cancellation citing this control yet the target file exists."
    ),
    "CANCELLATION_IDENTITY_MISMATCH": (
        "A ControlProof cancellation in Bob's record carries a wrong nonce or event."
    ),
    "HOOK_DIGEST_MISMATCH": (
        "A bound ledger row's hook_sha256 disagrees with the run's declared hook digest."
    ),
    "LEDGER_CONTRADICTS_INVALID_MATCHER": (
        "Bound ledger rows exist but Bob logged that the hook matcher for this event is invalid."
    ),
    "NOT_CONFIGURED_BUT_RAN": (
        "Ledger rows exist for this control but it is not present in the Bob configuration."
    ),
    "BOB_VERSION_MISMATCH": (
        "RUN.json bob_version and bob_product_version normalize to different values."
    ),
}

# Ordered list of conflict codes (determines detection order)
_CONFLICT_ORDER = [
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
]


def _make_conflict(code: str, detail: str, artifacts: tuple[str, ...]) -> Conflict:
    return Conflict(
        code=code,
        artifacts=artifacts,
        detail=detail,
        affects=CONFLICT_AFFECTS[code],
    )


# ---------------------------------------------------------------------------
# Core detector
# ---------------------------------------------------------------------------


def detect_conflicts(
    ev: ControlEvidence,
    run: dict,
    evidence_dir: "str | Path",
) -> tuple[Conflict, ...]:
    """Detect conflicts for one control. Returns at most one Conflict per code."""
    conflicts: list[Conflict] = []
    bound_rows = ev.bound_rows
    facts = ev.facts
    control_id = facts.control_id
    workspace_path = run.get("controls", [{}])[0].get("workspace_path", "")
    # Find the matching control entry
    for ctrl in run.get("controls", []):
        if ctrl.get("control_id") == control_id:
            workspace_path = ctrl.get("workspace_path", "")
            break

    session_id = facts.session_id

    # LIFECYCLE_MISMATCH
    configured_event = facts.configured_event
    if configured_event is not None and bound_rows:
        mismatched = [
            r for r in bound_rows
            if r.get("hook_event_name") != configured_event
        ]
        if mismatched:
            conflicts.append(_make_conflict(
                "LIFECYCLE_MISMATCH",
                f"Bound row hook_event_name {mismatched[0].get('hook_event_name')!r} "
                f"differs from configured event {configured_event!r}.",
                ("config", "ledger"),
            ))

    # MULTIPLE_EVENTS
    if bound_rows:
        distinct_events = {r.get("hook_event_name") for r in bound_rows}
        if len(distinct_events) > 1:
            conflicts.append(_make_conflict(
                "MULTIPLE_EVENTS",
                f"Bound rows carry {len(distinct_events)} distinct hook_event_name values: "
                f"{sorted(str(e) for e in distinct_events)}.",
                ("ledger", "ledger"),
            ))

    # LEDGER_PATH_MISMATCH
    if bound_rows and ev.bob_tool_calls is not None:
        tc_by_id = {tc.id: tc for tc in ev.bob_tool_calls}
        for row in bound_rows:
            if row.get("tool_name") not in WRITE_TOOLS:
                continue
            tuid = row.get("tool_use_id", "")
            tname = row.get("tool_name", "")
            if not tuid:
                continue
            tc = tc_by_id.get(tuid)
            if tc is None or tc.name != tname:
                continue
            # Matching call found; compare paths
            row_rel = workspace_relative(row.get("tool_input_path"), workspace_path)
            call_rel = workspace_relative(tc.arguments.get("path"), workspace_path)
            if row_rel != call_rel:
                conflicts.append(_make_conflict(
                    "LEDGER_PATH_MISMATCH",
                    f"Ledger row tool_use_id={tuid!r} has path {row.get('tool_input_path')!r} "
                    f"but Bob's record has {tc.arguments.get('path')!r}.",
                    ("ledger", "bob_task"),
                ))
                break  # at most one per code

    # DUPLICATE_DISAGREES
    _DUPE_FIELDS = ("hook_event_name", "tool_name", "tool_input_path", "decision",
                    "policy_triggered", "hook_sha256")
    if bound_rows:
        by_tuid: dict[str, list[dict]] = {}
        for row in bound_rows:
            tuid = row.get("tool_use_id", "")
            if not tuid:
                continue
            by_tuid.setdefault(tuid, []).append(row)
        for tuid, rows in by_tuid.items():
            if len(rows) < 2:
                continue
            first = rows[0]
            for other in rows[1:]:
                if any(first.get(f) != other.get(f) for f in _DUPE_FIELDS):
                    conflicts.append(_make_conflict(
                        "DUPLICATE_DISAGREES",
                        f"Two rows with tool_use_id={tuid!r} differ in a load-bearing field.",
                        ("ledger", "ledger"),
                    ))
                    break
            else:
                continue
            break  # at most one per code

    # CANCELLED_BUT_TARGET_EXISTS
    if facts.bob_cancelled_citing_control is True and facts.target_exists_after is True:
        conflicts.append(_make_conflict(
            "CANCELLED_BUT_TARGET_EXISTS",
            "Bob's record shows a cancellation citing this control, "
            "but the target file exists in the after-state snapshot.",
            ("bob_task", "snapshot"),
        ))

    # CANCELLATION_IDENTITY_MISMATCH
    if ev.bob_tool_calls is not None and bound_rows:
        run_nonce = run.get("run_nonce", "")
        actual_event = facts.actual_event
        for tc in ev.bob_tool_calls:
            if tc.name not in WRITE_TOOLS:
                continue
            canc = cancellation_for_call(tc)
            if canc is None or canc["control_id"] != control_id:
                continue
            # This is a cancellation attributed to this control
            nonce_ok = (canc["nonce"] == run_nonce)
            event_ok = True
            if actual_event is not None and canc["event"] is not None:
                event_ok = (canc["event"] == actual_event)
            elif actual_event is not None and canc["event"] is None:
                event_ok = False
            if not nonce_ok or not event_ok:
                conflicts.append(_make_conflict(
                    "CANCELLATION_IDENTITY_MISMATCH",
                    f"Cancellation for {control_id!r} has nonce={canc['nonce']!r} "
                    f"(expected {run_nonce!r}) and event={canc['event']!r} "
                    f"(expected {actual_event!r}).",
                    ("bob_task", "run"),
                ))
                break

    # HOOK_DIGEST_MISMATCH
    hook_info = run.get("hook")
    declared_sha = hook_info.get("sha256") if isinstance(hook_info, dict) else None
    if bound_rows:
        row_shas = {r.get("hook_sha256") for r in bound_rows if r.get("hook_sha256") is not None}
        if isinstance(declared_sha, str):
            mismatched_sha = any(sha != declared_sha for sha in row_shas)
            if mismatched_sha:
                conflicts.append(_make_conflict(
                    "HOOK_DIGEST_MISMATCH",
                    f"A bound row's hook_sha256 differs from the run's declared "
                    f"hook sha256 {declared_sha!r}.",
                    ("run", "ledger"),
                ))
        elif len(row_shas) > 1:
            conflicts.append(_make_conflict(
                "HOOK_DIGEST_MISMATCH",
                f"Bound rows carry {len(row_shas)} distinct hook_sha256 values.",
                ("run", "ledger"),
            ))

    # LEDGER_CONTRADICTS_INVALID_MATCHER
    if bound_rows and ev.session_bound and session_id:
        workspace = ""
        for ctrl in run.get("controls", []):
            if ctrl.get("control_id") == control_id:
                workspace = ctrl.get("workspace", "")
                break
        settings = load_settings(evidence_dir, workspace)
        cfg_pairs = control_config(settings, control_id)
        log_lines = bob_log_lines(evidence_dir, session_id)
        bound_events = {r.get("hook_event_name") for r in bound_rows}
        for event_val, matcher_val in cfg_pairs:
            if matcher_val is None:
                continue
            if event_val not in bound_events:
                continue
            expected_msg = f'Ignoring invalid {event_val} hook matcher "{matcher_val}"'
            if any(line.get("msg") == expected_msg for line in log_lines):
                conflicts.append(_make_conflict(
                    "LEDGER_CONTRADICTS_INVALID_MATCHER",
                    f"Bound rows exist for event {event_val!r} but Bob logged that "
                    f"matcher {matcher_val!r} is invalid.",
                    ("bob_log", "ledger"),
                ))
                break

    # NOT_CONFIGURED_BUT_RAN
    if facts.configured is False and facts.ledger_corroborated is True:
        conflicts.append(_make_conflict(
            "NOT_CONFIGURED_BUT_RAN",
            f"Control {control_id!r} has corroborated ledger rows but is not present "
            "in the Bob configuration.",
            ("config", "ledger"),
        ))

    # BOB_VERSION_MISMATCH
    from controlproof.ingest import normalize_bob_version
    run_bv = normalize_bob_version(run.get("bob_version"))
    run_bpv = normalize_bob_version(run.get("bob_product_version"))
    if run_bv is not None and run_bpv is not None and run_bv != run_bpv:
        conflicts.append(_make_conflict(
            "BOB_VERSION_MISMATCH",
            f"RUN.json bob_version normalizes to {run_bv!r} but bob_product_version "
            f"normalizes to {run_bpv!r}.",
            ("run", "run"),
        ))

    # Sort by canonical order, deduplicate by code
    seen: set[str] = set()
    ordered: list[Conflict] = []
    for code in _CONFLICT_ORDER:
        for c in conflicts:
            if c.code == code and code not in seen:
                ordered.append(c)
                seen.add(code)

    return tuple(ordered)


# ---------------------------------------------------------------------------
# ledger_rows count helper
# ---------------------------------------------------------------------------


def _count_ledger_rows(bound_rows: tuple[dict, ...]) -> int:
    """Count distinct non-empty tool_use_ids plus rows with empty tool_use_id."""
    seen_tuids: set[str] = set()
    count = 0
    for row in bound_rows:
        tuid = row.get("tool_use_id", "")
        if not tuid:
            count += 1
        elif tuid not in seen_tuids:
            seen_tuids.add(tuid)
            count += 1
    return count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def assess(evidence_dir: "str | Path") -> list[ControlEvidence]:
    """Run ingest, then detect conflicts and set ledger_rows for each control."""
    evidence_dir = Path(evidence_dir)
    from controlproof.ingest import load_run
    run = load_run(evidence_dir)

    evs = ingest(evidence_dir)
    result: list[ControlEvidence] = []
    for ev in evs:
        conflicts = detect_conflicts(ev, run, evidence_dir)
        ledger_rows = _count_ledger_rows(ev.bound_rows)
        new_facts = dataclasses.replace(
            ev.facts,
            conflicts=conflicts,
            ledger_rows=ledger_rows,
        )
        result.append(dataclasses.replace(ev, facts=new_facts))
    return result
