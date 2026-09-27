# B6 — Show Bob 2.2.0's block text in the receipt (one-function fix)

## Why

The final audit found that the judge page's "View Evidence" shows `Bob cancellation text: —` for
the PRETOOLUSE control, although its verdict (ENFORCEMENT VERIFIED) rests on exactly that Bob
record. `controlproof/receipt.py` `_bob_cancellation_text` still recognises only the Bob 2.1.0
form via `parse_cancellation`; B5 taught ingestion the Bob 2.2.0 stored form through
`controlproof.ingest.cancellation_for_call`, but the receipt helper was out of B5's scope.

## Files in scope

- `controlproof/receipt.py` — only `_bob_cancellation_text` and its import line
- `tests/test_receipt_cancellation_text.py` (new)

Do not change any other file or any existing test.

## Change

In `_bob_cancellation_text`, replace `parse_cancellation(tc.result)` with
`cancellation_for_call(tc)` (import it from `controlproof.ingest`), and keep returning
`tc.result` for the first protected-path write call it recognises. Remove the
`parse_cancellation` import from `receipt.py` if nothing else there uses it. Nothing else
changes: the helper only fills the display field `evidence.bob_cancellation_text`; no result,
state or conflict depends on it.

## Acceptance tests (`tests/test_receipt_cancellation_text.py`)

Use `build_receipt` on a `tmp_path` copy of each fixture (verification of these unbundled copies
may fail; the test only reads `controls[i]["evidence"]["bob_cancellation_text"]`).

1. `test_bob22_block_text_shown` — `tests/fixtures/run-3arm-bob22/`: PRE's text starts with
   `ControlProof CP-001-PRE denied this write: `; POST and BADCFG are None.
2. `test_bob21_block_text_still_shown` — `tests/fixtures/run-3arm/`: PRE's text starts with
   `Tool call to write_file was cancelled: `.
3. `test_ordinary_error_not_shown` — in the bob22 copy, set PRE's blocked tool message content to
   `Failed to write file protected/test.txt: EACCES` (isError stays true): PRE's text is None.

## Stop condition

`python -m pytest -q` passes: the 106 existing tests unchanged plus these 3 (109). Then commit
only the two files in scope with this message, and stop:

```
bob(B6): show Bob 2.2.0 block text in the receipt

Implemented by IBM Bob from bob-tasks/B6-receipt-cancellation-text.md.
```

Do not push. Do not start another task.
