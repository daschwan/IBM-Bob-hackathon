# B5 — Read IBM Bob 2.2.0's stored cancellation form (surgical compatibility fix)

## Why

The live run on IBM Bob 2.2.0 showed that Bob's task store does not record a hook-blocked
call as `Tool call to <name> was cancelled: <reason>` (the form Bob 2.1.0 stored, and the only
form ControlProof reads today). Bob 2.2.0 stores the tool result as:

```json
{"role": "tool",
 "content": "ControlProof CP-001-PRE denied this write: deny writes under protected/. nonce=<nonce> event=PreToolUse",
 "toolUsage": {"signature": {"id": "<the call's tool_use_id>", "name": "write_file",
                             "arguments": {...}, "isError": true}}}
```

So ControlProof found no Bob-recorded cancellation and, correctly, did not claim ENFORCED. This
task teaches ingestion to read the 2.2.0 form **as well as** the 2.1.0 form, with every
attribution check intact. Nothing else changes.

Test data: `tests/fixtures/run-3arm-bob22/` — the synthetic three-arm run in the 2.2.0 stored
form (every tool result has `toolUsage.signature`; the blocked call has `isError: true` and the
hook reason as its whole content). Copy it to `tmp_path` before changing it.

## Files in scope

- `controlproof/ingest.py` — changes 1–3 below
- `controlproof/conflicts.py` — change 4 below only
- `tests/test_bob22_cancellation.py` (new)

Do not change any other file, any existing test, any fixture, `docs/`, the hook, the model,
bundle, receipt, render, capture or CLI code.

## Changes

1. **`BobToolCall`** gains three fields with defaults (so existing constructions still work):
   `result_is_error: object = None`, `result_signature_id: str | None = None`,
   `result_signature_name: str | None = None`. In `bob_tool_calls`, when a tool message is
   paired with a call, copy `data["toolUsage"]["signature"]["isError"]` (the raw value, not
   converted), `["id"]` and `["name"]` when `toolUsage` and `signature` are dicts; otherwise
   leave the defaults.

2. **`cancellation_for_call(call) -> dict | None`** (new, in `ingest.py`). Returns
   `{"form", "tool_name", "control_id", "nonce", "event"}` or None:
   - **Form `2.1`:** `parse_cancellation(call.result)` is not None and its `tool_name ==
     call.name` (unchanged rules).
   - **Form `2.2`:** all of: `call.result` is a `str`; `call.result_is_error is True`
     (identity — `"true"`, `1` do not count); `call.result_signature_id` is a non-empty string
     equal to `call.id`; `call.result_signature_name == call.name`; and `call.result` matches
     `^ControlProof (\S+) denied this write: `. Then `tool_name = call.name`, `control_id` = the
     captured id, `nonce` / `event` from `nonce=(\S+)` / `event=(\S+)` (None when absent).
   - Anything else → None. `isError` alone, or the word "denied" alone, is never enough.

3. **Ingest attribution** of `bob_cancelled_citing_control` uses `cancellation_for_call(tc)`
   instead of `parse_cancellation(tc.result)` plus the tool-name check. A call counts only if
   the returned `control_id` equals the control's id exactly. Everything else in that rule
   (protected-path calls only, NOT_DETERMINED when a result is missing or not a string) stays.

4. **`CANCELLATION_IDENTITY_MISMATCH`** in `conflicts.py` uses `cancellation_for_call(tc)` in
   place of `parse_cancellation(tc.result)`, so a 2.2.0-form cancellation with the wrong nonce or
   event is caught exactly like a 2.1.0-form one. The conflict's rule and `affects` are unchanged.

## Acceptance tests (`tests/test_bob22_cancellation.py`)

`result(ev)` = `evaluate(replace(ev.facts, bundle_verified=True))` using `assess` on a
`tmp_path` copy. "PRE blocked message" = the tool message answering the protected-path call in
PRE's task export.

1. `test_bob21_form_still_attributed` — `tests/fixtures/run-3arm/` (2.1.0 form): results
   ENFORCEMENT_VERIFIED, OBSERVATIONAL_ONLY, CONFIGURED_NOT_EXECUTED.
2. `test_bob22_form_three_arms` — `run-3arm-bob22`: PRE bob_cancelled_citing_control True and
   ENFORCEMENT_VERIFIED; POST OBSERVATIONAL_ONLY; BADCFG CONFIGURED_NOT_EXECUTED; no conflicts.
3. `test_is_error_false_not_attributed` — PRE blocked message `isError` false: not attributed,
   PRE result NOT_DETERMINED.
4. `test_is_error_string_true_not_attributed` — `isError` `"true"`, and separately `1`: not
   attributed.
5. `test_wrong_control_id` — content cites `CP-OTHER`, and separately `CP-001-PRE-X`: not
   attributed, PRE not ENFORCEMENT_VERIFIED.
6. `test_wrong_nonce` — `nonce=` changed in the content: `CANCELLATION_IDENTITY_MISMATCH`,
   CONFLICTING_EVIDENCE, enforced NOT_DETERMINED.
7. `test_wrong_event` — `event=PostToolUse` in the content: `CANCELLATION_IDENTITY_MISMATCH`,
   CONFLICTING_EVIDENCE.
8. `test_wrong_signature_id` — `signature.id` differs from the call id: not attributed.
9. `test_wrong_signature_name` — `signature.name` `apply_diff`: not attributed.
10. `test_ordinary_tool_error` — content `Failed to write file protected/test.txt: EACCES` with
    `isError` true: not attributed, PRE not ENFORCEMENT_VERIFIED.
11. `test_non_string_content` — content as a list of text parts with `isError` true: no
    exception, bob_cancelled_citing_control NOT_DETERMINED.
12. `test_missing_signature` — `toolUsage` removed from the PRE blocked message: not attributed
    (the 2.2.0 form needs Bob's call link), PRE NOT_DETERMINED.
13. `test_unit_cancellation_for_call` — direct `BobToolCall` cases: valid 2.1 form, valid 2.2
    form, and each single broken condition of the 2.2 form returning None.

## Stop condition

`python -m pytest -q` passes: the 93 existing tests unchanged plus these 13 (106). Then commit
only the three files in scope with this message, and stop:

```
bob(B5): read IBM Bob 2.2.0 stored cancellation form

Implemented by IBM Bob from bob-tasks/B5-bob22-cancellation-form.md.
```

Do not push. Do not start another task.
