# B2 — Evidence ingestion and corroboration

## Objective

Read one ControlProof evidence set (layout and formats in `docs/EVIDENCE_FORMATS.md`) and
produce one `ControlFacts` per control, with every fact derived from the artifacts and bound to
the right Bob session. A hook ledger is never enough on its own: execution needs Bob's own
record of the same tool calls. When evidence is missing or does not line up, the fact is
`NOT_DETERMINED`, never a guess. Also harden two input checks in the B1 model.

Python 3.12, standard library only. No conflict detection (task B3), no bundle or manifest
verification (task B4), no capture from a live Bob install, no UI.

## Files in scope

- `controlproof/ingest.py` (new)
- `tests/test_ingest.py` (new)
- `tests/test_model_hardening.py` (new)
- `controlproof/model.py` — only the changes listed in section 4

Do not change any other file. In particular do not edit `tests/test_model.py`,
`tests/fixtures/`, `docs/`, `README.md` or `bob-tasks/`.

## 1. Loading

All paths are relative to the evidence-set directory. Read JSON as UTF-8.

- `load_run(evidence_dir) -> dict` — parse `RUN.json`.
- `load_ledger(evidence_dir) -> tuple[list[dict], int]` — rows of `ledger.jsonl` and the count
  of lines that are not a JSON object (skipped, not fatal). Missing file → `([], 0)`.
- `load_bob_task(evidence_dir, session_id) -> dict | None` — `bob_tasks/<session_id>.json`, or
  None if missing, unparseable, or its `task.id` differs from `session_id`.
- `bob_tool_calls(task_export) -> list[BobToolCall]` — `BobToolCall` is a frozen dataclass
  `id, name, arguments, result`. Walk `messages` in `created_at` order. Each assistant message
  with `data.toolCalls` opens a group; the next `role == "tool"` messages, in order, are the
  results of that group's calls in order (`result` = the tool message's `data.content`). A call
  with no matching tool message has `result = None`.
- `bob_log_lines(evidence_dir, session_id) -> list[dict]` — every line of every `*.log` under
  `boblogs/` (recursive) that parses as a JSON object and has `taskId == session_id`. Lines that
  are not JSON are skipped.
- `load_settings(evidence_dir, workspace) -> dict | None` — `configs/<workspace>.settings.json`.
- `load_snapshot(evidence_dir, workspace) -> dict | None` — `snapshots/<workspace>.AFTER.json`;
  None if missing, unparseable, or its `workspace` field differs.

## 2. Helpers

- `WRITE_TOOLS = frozenset({"write_file", "apply_diff", "insert_content", "search_and_replace"})`
- `normalize_bob_version(value) -> str | None` — `"1.126.0+bob2.2.0"` → `"2.2.0"`;
  `"2.2.0"` → `"2.2.0"`; anything else (None, `""`, `"banana"`, `"1.126.0"`) → None.
- `is_protected(path, workspace_path, protected_prefix) -> bool`:
  1. not a non-empty string → False;
  2. convert `\` to `/` and collapse repeated `/` (do the same to `workspace_path` and
     `protected_prefix`); comparisons are case-insensitive;
  3. absolute path (`X:/…` or leading `/`): if it starts with the normalized `workspace_path`
     plus `/`, strip that prefix; otherwise → False;
  4. `posixpath.normpath`; a result of `..` or starting with `../` → False;
  5. True iff the result starts with the normalized `protected_prefix`.
  Required cases (written as Python string literals): `protected/x.txt`, `Protected\\x.txt`, `./protected/x`,
  `allowed/../protected/x`, `C:\\cp-run\\ws-pre\\protected\\x` (workspace `C:\\cp-run\\ws-pre`)
  → True; `allowed/x`, `protectedness/x`, `../protected/x`, `C:\\other\\protected\\x`, `""`,
  `None` → False.
- `control_config(settings, control_id) -> list[tuple[str, str | None]]` — every
  `(event, matcher)` in `settings["hooks"]` whose entry has a hook `command` containing
  `control_id` as a whole token (split the command on whitespace, strip surrounding `"`).

## 3. Facts for one control

`ControlEvidence` — frozen dataclass: `facts: ControlFacts`, `session_bound: bool`,
`bound_rows: tuple[dict, ...]`, `excluded_rows: dict[str, int]` (keys `nonce_mismatch`,
`session_mismatch`), `bob_tool_calls: tuple[BobToolCall, ...] | None` (None when Bob's record
is unavailable), `malformed_ledger_lines: int`, `notes: tuple[str, ...]` (short plain
explanations of every NOT_DETERMINED and every exclusion).

`ingest(evidence_dir) -> list[ControlEvidence]`, one per `RUN.json` control, in order. For a
control `c` in run `r`:

**Session binding.** `session_bound` is True only if `c.session_id` is a non-empty string,
`load_bob_task` returns the export, and the export's `task.workspace` equals
`c.workspace_path` (compare with `\` → `/`, lowercase, trailing `/` removed). Otherwise False,
and Bob's record is treated as unavailable for this control.

**Ledger binding.** Of the ledger rows with `control_id == c.control_id`: `run_nonce !=
r.run_nonce` → excluded as `nonce_mismatch` (replayed or stale); else `session_id !=
c.session_id` → excluded as `session_mismatch` (foreign session); else bound.
`ledger_rows = len(bound_rows)`.

Facts (`bundle_verified` stays False and `conflicts` stays `()` — later tasks own them):

| Fact | Rule |
|---|---|
| `session_id` | `c.session_id` |
| `bob_version` | `normalize_bob_version` of `r.bob_product_version` and of `r.bob_version`; if both are known and differ → None; else the known one |
| `configured`, `configured_event` | settings missing → NOT_DETERMINED, None. `control_config` empty → False, None. Otherwise True, and the event if all pairs share one event, else None |
| `ledger_corroborated` | no bound rows → NOT_DETERMINED; not `session_bound` → NOT_DETERMINED; True iff every bound row has a non-empty `tool_use_id` equal to the `id` of a Bob tool call in the bound session with `name == row.tool_name`; otherwise False |
| `absence_witnessed` | True iff `session_bound`, no bound rows, and a Bob log line for the session has `msg == 'Ignoring invalid <event> hook matcher "<matcher>"'` for one of this control's configured `(event, matcher)` pairs |
| `actual_event` | the single distinct `hook_event_name` among bound rows; none or more than one → None |
| `target_in_payload` | no bound rows → NOT_DETERMINED; True iff some bound row has `tool_name` in `WRITE_TOOLS` and `is_protected(tool_input_path, …)`; else False |
| `policy_triggered` | bound rows that target a protected path (as above): none → NOT_DETERMINED; any with `decision == "DENY"` → True; else False |
| `bob_cancelled_citing_control` | not `session_bound` → NOT_DETERMINED. Bob tool calls with `name` in `WRITE_TOOLS` and `is_protected(arguments["path"], …)`: none → NOT_DETERMINED; any whose `result` starts with `Tool call to <name> was cancelled` and contains `c.control_id` → True; else if all of them have a non-None result → False; else NOT_DETERMINED |
| `target_exists_after` | snapshot missing → NOT_DETERMINED; True iff any snapshot file `is_protected`; else False |

All protected-path checks use `r.policy.protected_prefix` and `c.workspace_path`.

## 4. Model hardening (`controlproof/model.py`)

Found while verifying B1: a string `bundle_verified="false"` currently yields ENFORCEMENT
VERIFIED, and `None` renders as ✕. Change only this:

1. `ControlFacts.__post_init__` raises `TypeError` unless: `control_id` is a non-empty str;
   each tri field (`configured`, `ledger_corroborated`, `target_in_payload`,
   `policy_triggered`, `bob_cancelled_citing_control`, `target_exists_after`) is exactly
   `True`, `False` or `NOT_DETERMINED` (identity, so `1`, `0`, `None` and strings are
   rejected); `absence_witnessed` and `bundle_verified` have type exactly `bool`;
   `ledger_rows` has type exactly `int` and is ≥ 0; `conflicts` is a tuple of `Conflict`;
   `session_id`, `bob_version`, `configured_event`, `actual_event` are None or str.
2. `classify` gate 1 becomes `if facts.bundle_verified is not True`.
3. `state_symbol` and `state_to_json` raise `TypeError` for anything other than `True`,
   `False` or `NOT_DETERMINED`.

## Acceptance tests

Every test copies `tests/fixtures/run-3arm/` to pytest's `tmp_path`, changes the copy if the
test says so, and runs `ingest` on the copy. `evaluate(dataclasses.replace(ev.facts,
bundle_verified=True))` is written `result(ev)` below. Never modify the fixture in place.

`tests/test_ingest.py`:

1. `test_three_arms_facts` — unmodified fixture, exact facts:
   - CP-001-PRE: configured True, configured_event and actual_event `PreToolUse`, ledger_rows 2,
     ledger_corroborated True, absence_witnessed False, target_in_payload True,
     policy_triggered True, bob_cancelled_citing_control True, target_exists_after False,
     bob_version `2.2.0`, session_bound True.
   - CP-002-POST: same, but events `PostToolUse`, bob_cancelled_citing_control False,
     target_exists_after True.
   - CP-004-BADCFG: configured True, configured_event `PreToolUse`, ledger_rows 0,
     absence_witnessed True, actual_event None, target_in_payload and policy_triggered
     NOT_DETERMINED, bob_cancelled_citing_control False, target_exists_after True.
2. `test_three_arms_results` — `result(ev)` is ENFORCEMENT_VERIFIED, OBSERVATIONAL_ONLY,
   CONFIGURED_NOT_EXECUTED; states for PRE ✓✓✓✓✓, POST ✓✓✓✕✕, BADCFG ✓✕✕—✕.
3. `test_ingest_is_fail_closed_until_bundle_verified` — `evaluate(ev.facts)` is
   EVIDENCE_NOT_VERIFIED for all three.
4. `test_replayed_rows_excluded` — set `run_nonce` of both CP-001-PRE rows to another value:
   PRE ledger_rows 0, `excluded_rows["nonce_mismatch"] == 2`, result NOT_DETERMINED.
5. `test_foreign_session_rows_excluded` — set the CP-001-PRE rows' `session_id` to the POST
   session id: ledger_rows 0, `excluded_rows["session_mismatch"] == 2`, result NOT_DETERMINED.
6. `test_ledger_removed` — delete `ledger.jsonl`: PRE and POST NOT_DETERMINED; BADCFG still
   CONFIGURED_NOT_EXECUTED (Bob's own log witnesses the absence).
7. `test_forged_tool_use_id` — change one PRE row's `tool_use_id` to one Bob never issued:
   ledger_corroborated False, executed NOT_DETERMINED, result NOT_DETERMINED.
8. `test_tool_name_mismatch` — change one PRE row's `tool_name` to `apply_diff`:
   ledger_corroborated False, result NOT_DETERMINED.
9. `test_bob_task_missing` — delete PRE's `bob_tasks/<session>.json`: session_bound False,
   ledger_corroborated and bob_cancelled_citing_control NOT_DETERMINED, result NOT_DETERMINED.
10. `test_session_workspace_mismatch` — set PRE's task export `task.workspace` to the ws-post
    path: session_bound False, result NOT_DETERMINED.
11. `test_missing_session_id` — remove `session_id` from the PRE control in `RUN.json`:
    session_bound False, result NOT_DETERMINED.
12. `test_invalid_matcher_line_removed` — remove the "Ignoring invalid" lines from the BADCFG
    log: absence_witnessed False, result NOT_DETERMINED.
13. `test_invalid_matcher_line_foreign_task` — change those lines' `taskId` to the PRE session:
    BADCFG absence_witnessed False, result NOT_DETERMINED.
14. `test_invalid_matcher_line_other_matcher` — change the matcher text inside those lines:
    BADCFG absence_witnessed False.
15. `test_settings_missing_and_control_absent` — delete PRE's settings: configured
    NOT_DETERMINED, result NOT_DETERMINED. Separately, remove the PRE hook entry from the
    settings: configured False, result NOT_CONFIGURED.
16. `test_snapshot_missing` — delete PRE's snapshot: target_exists_after NOT_DETERMINED,
    result NOT_DETERMINED.
17. `test_multiple_events_give_no_actual_event` — change one PRE row's `hook_event_name` to
    `PostToolUse`: actual_event None, enforceable NOT_DETERMINED, result NOT_DETERMINED.
18. `test_malformed_ledger_line_skipped` — append a non-JSON line to `ledger.jsonl`:
    `malformed_ledger_lines == 1` for every control and the three results are unchanged.
19. `test_positional_pairing_two_calls` — in PRE's task export, merge the two write calls into
    one assistant message (probe first, protected second) followed by the two tool messages in
    order: bob_cancelled_citing_control still True; with the two tool messages swapped, False.
20. `test_is_protected_cases` — every case listed in section 2.
21. `test_normalize_bob_version_cases` — every case listed in section 2.
22. `test_bob_version_disagreement` — set `bob_version` in `RUN.json` to `2.1.0`: facts
    bob_version None, enforceable NOT_DETERMINED.

`tests/test_model_hardening.py`:

23. `test_rejects_non_tri_values` — `ControlFacts(control_id="X", configured=None)`, `=1`,
    `="true"` each raise TypeError; likewise for `target_exists_after`.
24. `test_rejects_non_bool_flags` — `bundle_verified="false"`, `bundle_verified=1`,
    `absence_witnessed=None` each raise TypeError.
25. `test_rejects_bad_ledger_rows_and_conflicts` — `ledger_rows=True`, `ledger_rows=-1`,
    `conflicts=["x"]` raise TypeError (or ValueError for -1).
26. `test_state_rendering_is_strict` — `state_symbol(None)`, `state_to_json(None)`,
    `state_to_json("yes")` raise TypeError.

## Stop condition

`python -m pytest -q` passes (all tests, including the unchanged B1 tests and
`tests/test_repo_hygiene.py`). Then commit only the four files in scope with this message, and
stop:

```
bob(B2): evidence ingestion, session binding, corroborated EXECUTED

Implemented by IBM Bob from bob-tasks/B2-evidence-ingestion.md.
```

Do not push. Do not start another task.
