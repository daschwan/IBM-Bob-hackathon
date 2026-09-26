# B3 — Hook evidence and conflict detection

## Objective

1. Build the ControlProof hook: the small standalone script IBM Bob runs as a hook, which
   applies the demo policy (no writes under `protected/`) and appends one ledger row per call.
2. Detect **conflicts**: two *available* artifacts that disagree about something load-bearing.
   A conflict withdraws the states it disputes and makes the result
   `CONTROL_CONFLICTING_EVIDENCE`. Missing evidence is not a conflict; it stays
   `NOT_DETERMINED`.
3. Close five gaps found while verifying B2 (section 3).

Formats: `docs/EVIDENCE_FORMATS.md`. Test data: `tests/fixtures/run-3arm/` (copy to `tmp_path`,
never change it in place). Python 3.12, standard library only. No bundle or manifest work
(B4), no capture from a live Bob install (B4), no UI (B5).

## Files in scope

Create:
- `hook/controlproof_hook.py`
- `controlproof/conflicts.py`
- `tests/test_hook.py`
- `tests/test_conflicts.py`

Modify, only as listed:
- `controlproof/ingest.py` — the four changes in section 3
- `controlproof/model.py` — the two changes in section 4

Do not change any other file. Do not edit any existing test file, `tests/fixtures/`, `docs/`,
`README.md`, `bob-tasks/` or `bob_sessions/`.

## 1. The hook (`hook/controlproof_hook.py`)

Standalone: standard library only, and it must not import `controlproof` (its bytes are pinned
by digest, so everything it runs must be in this one file).

Command line: `controlproof_hook.py <CONTROL_ID> --ledger <path> --nonce <nonce>
[--protected-prefix protected/]`. Missing or bad arguments: message on stderr, exit 1, no ledger
row.

Behaviour for one call:
1. Read stdin as UTF-8 and parse it as JSON. If that fails or is not an object: append a row with
   `decision: "ERROR"` (payload fields null), exit 1.
2. `tool_input_path` = `tool_input["path"]` when it is a string, else null.
3. `policy_triggered` = `tool_name` is one of `write_file`, `apply_diff`, `insert_content`,
   `search_and_replace`, and the path is protected under the payload's `cwd` (same rules as
   `controlproof.ingest.is_protected`, re-implemented in the hook as `is_protected(path,
   workspace_path, protected_prefix)`).
4. `decision` = `"DENY"` if triggered, else `"ALLOW"`.
5. Append one JSON line to the ledger (open in append mode, write the whole line at once):
   `schema` (`controlproof.ledger/1`), `control_id`, `run_nonce`, `session_id`,
   `hook_event_name`, `tool_name`, `tool_use_id`, `tool_input_path`, `decision`,
   `policy_triggered`, `has_tool_response` (payload has a `tool_response` key), `exit_code`,
   `hook_name` (`controlproof-hook`), `hook_version` (`1.0.0`), `hook_sha256` (sha256 of the
   hook file's own bytes), `hook_invoked_at` (UTC, `YYYY-MM-DDTHH:MM:SS.mmmZ`).
6. DENY: print exactly
   `ControlProof <CONTROL_ID> denied this write: deny writes under protected/. nonce=<nonce> event=<hook_event_name>`
   to stderr and exit 2. ALLOW: exit 0, nothing on stderr.

The hook behaves the same at every lifecycle event. Whether its exit 2 can stop anything is
Bob's decision, which ControlProof reads from the lifecycle-authority table — never from the
hook. The hook's own row never makes a control EXECUTED; ingestion still requires Bob's record.

## 2. Conflict detection (`controlproof/conflicts.py`)

`assess(evidence_dir) -> list[ControlEvidence]`: run `ingest`, then for each control replace
`facts` with `dataclasses.replace(facts, conflicts=detect_conflicts(ev, run, evidence_dir),
ledger_rows=<number of distinct non-empty tool_use_ids among bound rows, plus rows with an empty
tool_use_id>)`. `bundle_verified` stays False. `ingest` itself keeps returning `conflicts=()`.

`detect_conflicts(ev, run, evidence_dir) -> tuple[Conflict, ...]`, at most one conflict per code,
in the order below. Each `Conflict` gets `code`, `artifacts`, a one-sentence `detail`, and
`affects` from `CONFLICT_AFFECTS`. Also expose `CONFLICT_MEANINGS` (code → one sentence).

"Bound rows", "Bob calls" (`ev.bob_tool_calls`, None when the session is not bound) and the
workspace are those of the control. A Bob call *matches* a row when its `id == row.tool_use_id`
(non-empty) and `name == row.tool_name`.

| Code | Fires when | Artifacts | `affects` |
|---|---|---|---|
| `LIFECYCLE_MISMATCH` | `configured_event` is known and some bound row's `hook_event_name` differs from it | config, ledger | enforceable, enforced |
| `MULTIPLE_EVENTS` | bound rows carry more than one distinct `hook_event_name` | ledger, ledger | enforceable, enforced |
| `LEDGER_PATH_MISMATCH` | a bound row with `tool_name` in `WRITE_TOOLS` has a matching Bob call, and `workspace_relative(row.tool_input_path)` ≠ `workspace_relative(call.arguments.get("path"))` | ledger, bob_task | executed, observed, enforced |
| `DUPLICATE_DISAGREES` | two bound rows share a non-empty `tool_use_id` and differ in any of `hook_event_name`, `tool_name`, `tool_input_path`, `decision`, `policy_triggered`, `hook_sha256` | ledger, ledger | executed, observed, enforced |
| `CANCELLED_BUT_TARGET_EXISTS` | `bob_cancelled_citing_control is True` and `target_exists_after is True` | bob_task, snapshot | enforced |
| `CANCELLATION_IDENTITY_MISMATCH` | a Bob call to a protected path has a cancellation attributed to this control (section 3.2) whose `nonce=` value ≠ `run_nonce`, or whose `event=` value ≠ `actual_event` when `actual_event` is known | bob_task, run | enforced |
| `HOOK_DIGEST_MISMATCH` | `run["hook"]["sha256"]` is a string and a bound row's `hook_sha256` differs from it; or bound rows carry more than one distinct `hook_sha256` | run, ledger | executed, observed, enforceable, enforced |
| `LEDGER_CONTRADICTS_INVALID_MATCHER` | bound rows exist, the session is bound, and Bob's log for the session has `Ignoring invalid <event> hook matcher "<matcher>"` for one of the control's configured `(event, matcher)` pairs whose event equals some bound row's `hook_event_name` | bob_log, ledger | executed, observed, enforceable, enforced |
| `NOT_CONFIGURED_BUT_RAN` | `configured is False` and `ledger_corroborated is True` | config, ledger | configured |
| `BOB_VERSION_MISMATCH` | `RUN.json` `bob_version` and `bob_product_version` both normalize and differ | run, run | enforceable, enforced |

Rule for `affects`: a conflict may withdraw ENFORCEABLE only when it disputes *which lifecycle
event or which Bob version or which hook* produced the evidence. Conflicts about the outcome
(cancellation, after-state, paths, duplicates) never touch ENFORCEABLE.

On the clean fixture no conflict fires.

## 3. Changes to `controlproof/ingest.py` (B2 findings)

1. **Shared path normalization.** Add `workspace_relative(path, workspace_path) -> str | None`:
   steps 1–4 of `is_protected` (None where `is_protected` would return False before step 5),
   returning the normalized, lowercased, workspace-relative path. `is_protected` must use it.
2. **Exact cancellation attribution.** A result is a cancellation attributed to control `X` only
   if it is a string starting with `Tool call to <call name> was cancelled: ` and matches
   `ControlProof (\S+) denied this write` with the captured id exactly equal to `X`. A
   cancellation citing `CP-OTHER` or `CP-001-PRE-X` is not attributed to `CP-001-PRE`. Add a
   helper `parse_cancellation(result) -> dict | None` returning `control_id`, `nonce`, `event`
   (`nonce=(\S+)`, `event=(\S+)`; None when absent), used by ingest and by
   `CANCELLATION_IDENTITY_MISMATCH`.
3. **Non-string tool results.** In `bob_tool_calls`, a tool message whose `data.content` is not a
   string gives `result = None` (never `str(...)` of a structure). This makes
   `bob_cancelled_citing_control` NOT_DETERMINED instead of crashing.
4. **Malformed snapshot.** If `snapshot["files"]` is missing, not a list, or has a non-string
   element, `target_exists_after` is NOT_DETERMINED (with a note). Absence needs valid evidence.

## 4. Changes to `controlproof/model.py`

1. `Conflict` gains a last field `affects: tuple[str, ...] = ()`. `Conflict.__post_init__`
   raises `TypeError` unless every entry is in `STATES`.
2. `derive_states`: after deriving the five states, set every state named in any
   `facts.conflicts[i].affects` to NOT_DETERMINED. Nothing else changes.

## Acceptance tests

Unchanged: all 49 existing tests. New tests below (`result(ev)` = `evaluate(replace(ev.facts,
bundle_verified=True))`, using `assess`).

`tests/test_hook.py` — run the hook with `subprocess` and `sys.executable`, payload on stdin,
ledger in `tmp_path`:
1. `test_pretooluse_protected_write_denied` — exit 2, exact stderr text, one row with every
   field from section 1, `decision` DENY, `hook_sha256` = sha256 of the hook file.
2. `test_allowed_write_passes` — `allowed/probe.txt`: exit 0, empty stderr, row ALLOW.
3. `test_posttooluse_same_behaviour` — PostToolUse payload with `tool_response`: exit 2,
   `has_tool_response` True.
4. `test_non_write_tool_not_triggered` — `read_file` on `protected/x`: exit 0, ALLOW,
   `policy_triggered` False.
5. `test_absolute_path_under_cwd` — absolute path under the payload `cwd` in `protected\`:
   DENY.
6. `test_malformed_stdin` — `not json`: exit 1, one row with decision ERROR.
7. `test_rows_append` — two calls → two rows, first unchanged.
8. `test_hook_matches_ingest_normalization` — load the hook file with `importlib`; its
   `is_protected` agrees with `controlproof.ingest.is_protected` on every B2 section-2 case.
9. `test_hook_is_standalone` — the hook source imports nothing from `controlproof`.

`tests/test_conflicts.py` — copy the fixture to `tmp_path`, change the copy, run `assess`:
10. `test_clean_arms_no_conflicts` — no conflicts; results ENFORCEMENT_VERIFIED,
    OBSERVATIONAL_ONLY, CONFIGURED_NOT_EXECUTED; states as in B2.
11. `test_ledger_path_equal_after_normalization` — PRE protected row's path written as the
    absolute `C:\cp-run\ws-pre\protected\test.txt`: no conflict, result unchanged.
12. `test_ledger_path_mismatch` — PRE probe row's `tool_input_path` rewritten to
    `protected/test.txt`: `LEDGER_PATH_MISMATCH`, result CONFLICTING_EVIDENCE, executed /
    observed / enforced NOT_DETERMINED.
13. `test_cancellation_cites_other_control` — in PRE's cancellation text replace `CP-001-PRE`
    with `CP-OTHER`, and separately with `CP-001-PRE-X`: bob_cancelled_citing_control False, no
    conflict, result NOT_DETERMINED (never ENFORCEMENT_VERIFIED).
14. `test_malformed_snapshot_files` — PRE snapshot `files` as a string, as `[1, 2]`, and missing:
    target_exists_after NOT_DETERMINED, result NOT_DETERMINED.
15. `test_exact_duplicate_row_tolerated` — append an exact copy of PRE's DENY row: no conflict,
    ledger_rows 2, result ENFORCEMENT_VERIFIED.
16. `test_disagreeing_duplicate_row` — append PRE's DENY row with `decision` ALLOW:
    `DUPLICATE_DISAGREES`, result CONFLICTING_EVIDENCE.
17. `test_non_string_tool_result` — PRE's cancellation message content as a list of
    `{"type": "text", "text": <the cancellation text>}`, and as a dict: no exception,
    bob_cancelled_citing_control NOT_DETERMINED, result not ENFORCEMENT_VERIFIED.
18. `test_configured_vs_actual_event` — PRE settings key changed to `PostToolUse`:
    `LIFECYCLE_MISMATCH`, CONFLICTING_EVIDENCE, enforceable NOT_DETERMINED. One PRE row changed
    to `PostToolUse`: `MULTIPLE_EVENTS` and `LIFECYCLE_MISMATCH`.
19. `test_cancelled_but_target_exists` — add `protected/test.txt` to PRE's snapshot:
    `CANCELLED_BUT_TARGET_EXISTS`, CONFLICTING_EVIDENCE, enforced NOT_DETERMINED, enforceable
    still True.
20. `test_cancellation_identity_mismatch` — PRE cancellation `nonce=` changed, and separately
    `event=PostToolUse`: `CANCELLATION_IDENTITY_MISMATCH`, CONFLICTING_EVIDENCE.
21. `test_hook_digest_mismatch` — `RUN.json` gets `"hook": {"sha256": "1"*64}`: conflict on PRE
    and POST, not on BADCFG; with `"0"*64`: none. Two distinct digests among PRE rows: conflict.
22. `test_forged_rows_despite_invalid_matcher` — add two BADCFG rows (right nonce and session,
    PreToolUse, the BADCFG Bob call ids, names and paths): `LEDGER_CONTRADICTS_INVALID_MATCHER`,
    CONFLICTING_EVIDENCE.
23. `test_not_configured_but_ran` — remove PRE's hook entry from its settings:
    `NOT_CONFIGURED_BUT_RAN`, CONFLICTING_EVIDENCE (not NOT_CONFIGURED).
24. `test_bob_version_mismatch` — `RUN.json` `bob_version` = `2.1.0`: `BOB_VERSION_MISMATCH`.
25. `test_missing_evidence_is_not_conflict` — separately delete `ledger.jsonl`, PRE's snapshot,
    PRE's Bob task export: no conflicts; PRE result NOT_DETERMINED.
26. `test_conflict_table` — `CONFLICT_AFFECTS` has exactly the ten codes and `affects` above;
    none of `LEDGER_PATH_MISMATCH`, `DUPLICATE_DISAGREES`, `CANCELLED_BUT_TARGET_EXISTS`,
    `CANCELLATION_IDENTITY_MISMATCH`, `NOT_CONFIGURED_BUT_RAN` affects enforceable.
27. `test_conflict_withdraws_states` — model level: a `Conflict` with `affects=("observed",)`
    makes observed NOT_DETERMINED in `evaluate`; `affects=("banana",)` raises TypeError.
28. `test_ingest_contract_unchanged` — `ingest` on the clean fixture returns `conflicts == ()`
    for every control.

## Stop condition

`python -m pytest -q` passes: the 49 existing tests unchanged plus these 28. Then commit only
the six files in scope with this message, and stop:

```
bob(B3): hook evidence, conflict detection, evidence consistency

Implemented by IBM Bob from bob-tasks/B3-hook-and-conflicts.md.
```

Do not push. Do not start another task.
