"""ControlProof evidence ingestion — B2.

Read one evidence-set directory, produce one ControlFacts per control.
Python 3.12, standard library only. No conflict detection, no bundle verification.
"""

from __future__ import annotations

import dataclasses
import json
import posixpath
import re
from pathlib import Path
from typing import Any

from controlproof.model import (
    NOT_DETERMINED,
    Conflict,
    ControlFacts,
    tri,
)

# ---------------------------------------------------------------------------
# 1. Loading
# ---------------------------------------------------------------------------


def load_run(evidence_dir: "str | Path") -> dict:
    """Parse RUN.json from *evidence_dir*."""
    path = Path(evidence_dir) / "RUN.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_ledger(evidence_dir: "str | Path") -> tuple[list[dict], int]:
    """Parse ledger.jsonl; return (rows, malformed_count). Missing file → ([], 0)."""
    path = Path(evidence_dir) / "ledger.jsonl"
    if not path.exists():
        return ([], 0)
    rows: list[dict] = []
    malformed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                malformed += 1
        except json.JSONDecodeError:
            malformed += 1
    return (rows, malformed)


def load_bob_task(evidence_dir: "str | Path", session_id: str) -> "dict | None":
    """Load bob_tasks/<session_id>.json; None if missing, unparseable, or id mismatch."""
    path = Path(evidence_dir) / "bob_tasks" / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(obj, dict):
        return None
    task = obj.get("task", {})
    if not isinstance(task, dict):
        return None
    if task.get("id") != session_id:
        return None
    return obj


@dataclasses.dataclass(frozen=True)
class BobToolCall:
    """One tool call recorded in a Bob task export."""

    id: str
    name: str
    arguments: dict
    result: "str | None"


def bob_tool_calls(task_export: dict) -> list[BobToolCall]:
    """Extract BobToolCall list from a task export, in created_at order with positional pairing."""
    messages = task_export.get("messages", [])
    # Sort by created_at
    messages = sorted(messages, key=lambda m: m.get("created_at", 0))

    calls: list[BobToolCall] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant":
            tool_calls_data = msg.get("data", {}).get("toolCalls") or []
            if tool_calls_data:
                # Collect the tool result messages that immediately follow
                results: list["str | None"] = []
                j = i + 1
                while j < len(messages) and messages[j].get("role") == "tool":
                    result_content = messages[j].get("data", {}).get("content")
                    # Non-string content gives None (change 3)
                    if not isinstance(result_content, str):
                        result_content = None
                    results.append(result_content)
                    j += 1
                # Pair positionally
                for k, tc in enumerate(tool_calls_data):
                    result = results[k] if k < len(results) else None
                    calls.append(
                        BobToolCall(
                            id=tc.get("id", ""),
                            name=tc.get("name", ""),
                            arguments=tc.get("arguments", {}),
                            result=result,
                        )
                    )
                i = j
                continue
        i += 1
    return calls


def bob_log_lines(
    evidence_dir: "str | Path", session_id: str
) -> list[dict]:
    """Every JSON-object line from boblogs/**/*.log with taskId == session_id."""
    boblogs_dir = Path(evidence_dir) / "boblogs"
    if not boblogs_dir.exists():
        return []
    lines: list[dict] = []
    for log_file in sorted(boblogs_dir.rglob("*.log")):
        for raw_line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
                if isinstance(obj, dict) and obj.get("taskId") == session_id:
                    lines.append(obj)
            except json.JSONDecodeError:
                pass
    return lines


def load_settings(evidence_dir: "str | Path", workspace: str) -> "dict | None":
    """Load configs/<workspace>.settings.json; None if missing or unparseable."""
    path = Path(evidence_dir) / "configs" / f"{workspace}.settings.json"
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def load_snapshot(evidence_dir: "str | Path", workspace: str) -> "dict | None":
    """Load snapshots/<workspace>.AFTER.json; None if missing, unparseable, or workspace mismatch."""
    path = Path(evidence_dir) / "snapshots" / f"{workspace}.AFTER.json"
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("workspace") != workspace:
        return None
    return obj


# ---------------------------------------------------------------------------
# 2. Helpers
# ---------------------------------------------------------------------------

WRITE_TOOLS: frozenset[str] = frozenset(
    {"write_file", "apply_diff", "insert_content", "search_and_replace"}
)

_CANCEL_PREFIX_RE = re.compile(r"^Tool call to (\S+) was cancelled: ")
_CANCEL_RE = re.compile(r"ControlProof (\S+) denied this write")
_NONCE_RE = re.compile(r"nonce=(\S+)")
_EVENT_RE = re.compile(r"event=(\S+)")


def parse_cancellation(result: Any) -> "dict | None":
    """Parse a Bob tool result string for a ControlProof cancellation.

    Returns dict with keys 'tool_name', 'control_id', 'nonce' (or None),
    'event' (or None), or None if *result* is not a matching cancellation string.

    Requires the full Bob prefix ``Tool call to <tool_name> was cancelled: ``.
    A message like ``Tool call to write_file failed: ...`` is not a cancellation.
    """
    if not isinstance(result, str):
        return None
    # Must have the full standard Bob cancellation prefix
    prefix_m = _CANCEL_PREFIX_RE.match(result)
    if not prefix_m:
        return None
    tool_name = prefix_m.group(1)
    m = _CANCEL_RE.search(result)
    if not m:
        return None
    nonce_m = _NONCE_RE.search(result)
    event_m = _EVENT_RE.search(result)
    return {
        "tool_name": tool_name,
        "control_id": m.group(1),
        "nonce": nonce_m.group(1) if nonce_m else None,
        "event": event_m.group(1) if event_m else None,
    }


def normalize_bob_version(value: Any) -> "str | None":
    """Normalize a Bob product version string to its bob-version component.

    '1.126.0+bob2.2.0' → '2.2.0'; '2.2.0' → '2.2.0'; anything else → None.
    A bare X.Y.Z is accepted only when all three components are ≤ 99
    (distinguishes bob version '2.2.0' from product version '1.126.0').
    """
    if not isinstance(value, str) or not value:
        return None
    # Try +bobX.Y.Z pattern first (product version like '1.126.0+bob2.2.0')
    m = re.search(r"\+bob(\d+\.\d+\.\d+)$", value)
    if m:
        return m.group(1)
    # Try bare X.Y.Z — but only when it looks like a bob short version (all parts ≤ 99)
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{1,2})", value)
    if m:
        return value
    return None


def _norm_path(p: "str | None") -> str:
    """Normalize separators and collapse repeated slashes; lowercase."""
    if not p:
        return ""
    p = p.replace("\\", "/")
    # collapse repeated slashes (but preserve leading for absolute paths)
    while "//" in p:
        p = p.replace("//", "/")
    return p.lower()


def workspace_relative(path: "str | None", workspace_path: "str | None") -> "str | None":
    """Steps 1–4 of is_protected: normalize path to workspace-relative form.

    Returns the normalized, lowercased, workspace-relative path, or None when
    is_protected would return False before step 5.
    """
    # 1. not a non-empty string → None
    if not isinstance(path, str) or not path:
        return None

    norm_path = _norm_path(path)
    norm_ws = _norm_path(workspace_path).rstrip("/")

    # 3. absolute path handling
    is_absolute = bool(re.match(r"[a-z]:/", norm_path)) or norm_path.startswith("/")
    if is_absolute:
        if norm_ws and norm_path.startswith(norm_ws + "/"):
            norm_path = norm_path[len(norm_ws) + 1 :]
        else:
            return None

    # 4. posixpath.normpath; reject traversals
    norm_path = posixpath.normpath(norm_path)
    if norm_path == ".." or norm_path.startswith("../"):
        return None

    return norm_path


def is_protected(
    path: "str | None",
    workspace_path: "str | None",
    protected_prefix: "str | None",
) -> bool:
    """Return True iff *path* refers to a file under *protected_prefix* in *workspace_path*."""
    rel = workspace_relative(path, workspace_path)
    if rel is None:
        return False

    norm_prefix = _norm_path(protected_prefix).rstrip("/")

    # 5. check prefix (case-insensitive already via _norm_path)
    return rel == norm_prefix or rel.startswith(norm_prefix + "/")


def control_config(
    settings: "dict | None", control_id: str
) -> list[tuple[str, "str | None"]]:
    """Return (event, matcher) pairs whose hook command contains control_id as a whole token."""
    if not settings:
        return []
    hooks_section = settings.get("hooks", {})
    if not isinstance(hooks_section, dict):
        return []
    result: list[tuple[str, str | None]] = []
    for event, entries in hooks_section.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            matcher = entry.get("matcher") if isinstance(entry, dict) else None
            hooks = entry.get("hooks", []) if isinstance(entry, dict) else []
            for hook in hooks:
                if not isinstance(hook, dict):
                    continue
                command = hook.get("command", "")
                # Split on whitespace, strip surrounding quotes from each token
                tokens = [t.strip('"') for t in command.split()]
                if control_id in tokens:
                    result.append((event, matcher if isinstance(matcher, str) else None))
                    break  # one entry per hooks-list item is enough
    return result


# ---------------------------------------------------------------------------
# 3. Facts for one control
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ControlEvidence:
    """Derived evidence for one control."""

    facts: ControlFacts
    session_bound: bool
    bound_rows: tuple[dict, ...]
    excluded_rows: dict[str, int]
    bob_tool_calls: "tuple[BobToolCall, ...] | None"
    malformed_ledger_lines: int
    notes: tuple[str, ...]


def ingest(evidence_dir: "str | Path") -> list[ControlEvidence]:
    """Read one evidence set, return one ControlEvidence per control in RUN.json order."""
    evidence_dir = Path(evidence_dir)
    run = load_run(evidence_dir)
    run_nonce = run.get("run_nonce", "")
    policy = run.get("policy", {})
    protected_prefix = policy.get("protected_prefix", "")

    # Resolve bob_version from run-level fields
    run_bob_version = normalize_bob_version(run.get("bob_version"))
    run_bob_product_version = normalize_bob_version(run.get("bob_product_version"))
    if run_bob_version is not None and run_bob_product_version is not None:
        if run_bob_version != run_bob_product_version:
            resolved_bob_version: "str | None" = None
        else:
            resolved_bob_version = run_bob_version
    else:
        resolved_bob_version = run_bob_version or run_bob_product_version

    all_ledger_rows, malformed_ledger_lines = load_ledger(evidence_dir)
    results: list[ControlEvidence] = []

    for control in run.get("controls", []):
        control_id: str = control.get("control_id", "")
        session_id: "str | None" = control.get("session_id") or None
        workspace: str = control.get("workspace", "")
        workspace_path: str = control.get("workspace_path", "")
        notes: list[str] = []

        # --- Session binding ---
        session_bound = False
        task_export: "dict | None" = None
        tool_calls: "tuple[BobToolCall, ...] | None" = None

        if session_id:
            task_export = load_bob_task(evidence_dir, session_id)
            if task_export is not None:
                # Compare workspace: backslash→slash, lowercase, strip trailing /
                def _norm_ws(s: str) -> str:
                    return s.replace("\\", "/").lower().rstrip("/")

                export_ws = _norm_ws(task_export.get("task", {}).get("workspace", ""))
                control_ws = _norm_ws(workspace_path)
                if export_ws == control_ws:
                    session_bound = True
                    tool_calls = tuple(bob_tool_calls(task_export))
                else:
                    notes.append(
                        f"Session workspace mismatch: export has '{export_ws}', "
                        f"control expects '{control_ws}'."
                    )
            else:
                notes.append(f"Bob task export not found for session '{session_id}'.")
        else:
            notes.append("No session_id; session is unbound.")

        # --- Ledger binding ---
        bound_rows: list[dict] = []
        excluded_rows: dict[str, int] = {"nonce_mismatch": 0, "session_mismatch": 0}
        for row in all_ledger_rows:
            if row.get("control_id") != control_id:
                continue
            if row.get("run_nonce") != run_nonce:
                excluded_rows["nonce_mismatch"] += 1
                notes.append(
                    f"Ledger row excluded: run_nonce mismatch "
                    f"(got '{row.get('run_nonce')}', expected '{run_nonce}')."
                )
            elif row.get("session_id") != session_id:
                excluded_rows["session_mismatch"] += 1
                notes.append(
                    f"Ledger row excluded: session_id mismatch "
                    f"(got '{row.get('session_id')}', expected '{session_id}')."
                )
            else:
                bound_rows.append(row)

        # --- Configured ---
        settings = load_settings(evidence_dir, workspace)
        cfg_pairs = control_config(settings, control_id)
        if settings is None:
            configured: tri = NOT_DETERMINED
            configured_event: "str | None" = None
            notes.append("Settings file missing; configured is NOT_DETERMINED.")
        elif not cfg_pairs:
            configured = False
            configured_event = None
        else:
            configured = True
            events = {pair[0] for pair in cfg_pairs}
            configured_event = cfg_pairs[0][0] if len(events) == 1 else None

        # --- Ledger corroborated ---
        if not bound_rows:
            ledger_corroborated: tri = NOT_DETERMINED
            if not bound_rows:
                notes.append("No bound ledger rows; ledger_corroborated is NOT_DETERMINED.")
        elif not session_bound:
            ledger_corroborated = NOT_DETERMINED
            notes.append("Session not bound; ledger_corroborated is NOT_DETERMINED.")
        else:
            # tool_calls is not None when session_bound
            tc_map: dict[str, str] = {tc.id: tc.name for tc in (tool_calls or ())}
            all_corroborated = True
            for row in bound_rows:
                tuid = row.get("tool_use_id", "")
                tname = row.get("tool_name", "")
                if not tuid or tc_map.get(tuid) != tname:
                    all_corroborated = False
                    notes.append(
                        f"Ledger row tool_use_id='{tuid}' / tool_name='{tname}' "
                        "not corroborated by Bob's record."
                    )
                    break
            ledger_corroborated = True if all_corroborated else False

        # --- Absence witnessed ---
        absence_witnessed = False
        if session_bound and not bound_rows and cfg_pairs:
            log_lines = bob_log_lines(evidence_dir, session_id)  # type: ignore[arg-type]
            for event_val, matcher_val in cfg_pairs:
                if matcher_val is None:
                    continue
                expected_msg = f'Ignoring invalid {event_val} hook matcher "{matcher_val}"'
                for line in log_lines:
                    if line.get("msg") == expected_msg:
                        absence_witnessed = True
                        break
                if absence_witnessed:
                    break
            if not absence_witnessed:
                notes.append(
                    "No bound rows and no 'Ignoring invalid' log line; "
                    "absence_witnessed is False."
                )

        # --- actual_event ---
        if bound_rows:
            distinct_events = {row.get("hook_event_name") for row in bound_rows}
            actual_event: "str | None" = (
                next(iter(distinct_events)) if len(distinct_events) == 1 else None
            )
            if len(distinct_events) > 1:
                notes.append(
                    f"Multiple hook events in bound rows {distinct_events}; "
                    "actual_event is None."
                )
        else:
            actual_event = None

        # --- target_in_payload ---
        if not bound_rows:
            target_in_payload: tri = NOT_DETERMINED
        else:
            found = any(
                row.get("tool_name") in WRITE_TOOLS
                and is_protected(row.get("tool_input_path"), workspace_path, protected_prefix)
                for row in bound_rows
            )
            target_in_payload = True if found else False

        # --- policy_triggered ---
        targeting_rows = [
            row
            for row in bound_rows
            if row.get("tool_name") in WRITE_TOOLS
            and is_protected(row.get("tool_input_path"), workspace_path, protected_prefix)
        ]
        if not targeting_rows:
            policy_triggered: tri = NOT_DETERMINED
        elif any(row.get("decision") == "DENY" for row in targeting_rows):
            policy_triggered = True
        else:
            policy_triggered = False

        # --- bob_cancelled_citing_control ---
        if not session_bound:
            bob_cancelled: tri = NOT_DETERMINED
            notes.append(
                "Session not bound; bob_cancelled_citing_control is NOT_DETERMINED."
            )
        else:
            write_calls = [
                tc
                for tc in (tool_calls or ())
                if tc.name in WRITE_TOOLS
                and is_protected(tc.arguments.get("path"), workspace_path, protected_prefix)
            ]
            if not write_calls:
                bob_cancelled = NOT_DETERMINED
                notes.append(
                    "No write tool calls targeting protected path in Bob's record; "
                    "bob_cancelled_citing_control is NOT_DETERMINED."
                )
            else:
                cancelled = False
                all_have_result = True
                for tc in write_calls:
                    if tc.result is None:
                        all_have_result = False
                    else:
                        parsed = parse_cancellation(tc.result)
                        if (
                            parsed is not None
                            and parsed["control_id"] == control_id
                            and parsed["tool_name"] == tc.name
                        ):
                            cancelled = True
                            break
                if cancelled:
                    bob_cancelled = True
                elif all_have_result:
                    bob_cancelled = False
                else:
                    bob_cancelled = NOT_DETERMINED
                    notes.append(
                        "Some write calls have no result; "
                        "bob_cancelled_citing_control is NOT_DETERMINED."
                    )

        # --- target_exists_after ---
        snapshot = load_snapshot(evidence_dir, workspace)
        if snapshot is None:
            target_exists_after: tri = NOT_DETERMINED
            notes.append("Snapshot missing; target_exists_after is NOT_DETERMINED.")
        else:
            # Change 4: missing, non-list, or non-string element → NOT_DETERMINED
            if "files" not in snapshot:
                target_exists_after = NOT_DETERMINED
                notes.append(
                    "Snapshot files field is missing; target_exists_after is NOT_DETERMINED."
                )
            else:
                files = snapshot["files"]
                if not isinstance(files, list) or any(not isinstance(f, str) for f in files):
                    target_exists_after = NOT_DETERMINED
                    notes.append(
                        "Snapshot files field is malformed; target_exists_after is NOT_DETERMINED."
                    )
                else:
                    found_protected = any(
                        is_protected(f, workspace_path, protected_prefix) for f in files
                    )
                    target_exists_after = True if found_protected else False

        facts = ControlFacts(
            control_id=control_id,
            session_id=session_id,
            bob_version=resolved_bob_version,
            configured=configured,
            configured_event=configured_event,
            ledger_rows=len(bound_rows),
            ledger_corroborated=ledger_corroborated,
            absence_witnessed=absence_witnessed,
            actual_event=actual_event,
            target_in_payload=target_in_payload,
            policy_triggered=policy_triggered,
            bob_cancelled_citing_control=bob_cancelled,
            target_exists_after=target_exists_after,
            conflicts=(),
            bundle_verified=False,
        )

        results.append(
            ControlEvidence(
                facts=facts,
                session_bound=session_bound,
                bound_rows=tuple(bound_rows),
                excluded_rows=excluded_rows,
                bob_tool_calls=tool_calls,
                malformed_ledger_lines=malformed_ledger_lines,
                notes=tuple(notes),
            )
        )

    return results
