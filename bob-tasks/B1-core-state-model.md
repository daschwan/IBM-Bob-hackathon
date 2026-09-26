# B1 — Core state model

## Objective

Implement ControlProof's evidence schema and state model as a pure Python module: normalized
per-control evidence facts in, five tri-valued states and one result code out. No file I/O, no
parsing of Bob artifacts (that is task B2). Python 3.12, standard library only.

## Files in scope (create only these)

- `controlproof/__init__.py` (may be empty)
- `controlproof/model.py`
- `tests/test_model.py`

Do not change any other file.

## 1. Tri-valued states and NOT_DETERMINED

Every state is `True`, `False`, or `NOT_DETERMINED`. "We could not tell" is a real answer and is
never rounded to `False` or `True`.

- `NOT_DETERMINED` is a module-level singleton instance of a private class whose `__bool__`
  raises `TypeError`, so that code like `if state:` fails loudly instead of silently treating
  NOT_DETERMINED as true. Its `repr` is `NOT_DETERMINED`. Compare with `is`.
- `STATES = ("configured", "executed", "observed", "enforceable", "enforced")`
- `state_symbol(value)` returns `"✓"` for True, `"✕"` for False, `"—"` for NOT_DETERMINED.
- `state_to_json(value)` returns `True`, `False`, or the string `"not_determined"`.

## 2. Lifecycle authority (independent source of ENFORCEABLE)

Whether a lifecycle event can prevent a tool call is a property of the IBM Bob version, not of
any session outcome. It is represented explicitly, per Bob version. Nothing about what happened
in a session may feed back into it.

`LifecycleAuthority` — frozen dataclass: `bob_version: str`, `blocking: Mapping[str, bool]`
(event name → can a blocking hook exit at this event prevent the tool call), `source: str` (one
sentence naming where the table comes from). Method `blocking_capable(event)` returns the table
value, or `NOT_DETERMINED` for `None` or any event not in the table.

`AUTHORITIES: dict[str, LifecycleAuthority]` with exactly these two entries:

```python
"2.1.0": {"SessionStart": False, "UserPromptSubmit": True, "PreToolUse": True,
          "PostToolUse": False, "Stop": False}
"2.2.0": {"SessionStart": False, "UserPromptSubmit": True, "PreToolUse": True,
          "PostToolUse": False, "PostCompact": False, "Stop": False}
```

Sources (use these meanings for the `source` strings):
- 2.1.0: IBM Bob 1.126.0+bob2.1.0 hook runtime; only UserPromptSubmit and PreToolUse honour a
  blocking exit (exit code 2), other events log "<event> hooks cannot block"; observed in the
  author's pre-event research SPIKE-BOB-CONTROLPROOF-001 (2026-09-20).
- 2.2.0: IBM Bob 1.126.0+bob2.2.0 (commit 30bf4b86) hook runtime in the bob-code extension;
  exit code 2 blocks except at SessionStart, PostCompact, PostToolUse and Stop, which log
  "<event> hooks cannot block"; read from the installed extension on 2026-09-26. `PreCompact` is
  deliberately absent: it fires around context compaction, not around a tool call, and whether
  its blocking exit can prevent a tool call is not established, so it stays NOT_DETERMINED.

`authority_for(bob_version)` returns the matching entry by exact key, or `None`.

Module-level `blocking_capable(event, authority)` returns `NOT_DETERMINED` when `authority` is
`None`, otherwise `authority.blocking_capable(event)`.

## 3. Evidence schema

`Conflict` — frozen dataclass: `code: str`, `artifacts: tuple[str, ...]` (the artifacts that
disagree), `detail: str`.

`ControlFacts` — frozen dataclass, the normalized facts about one control in one Bob session.
Later tasks produce these from real Bob artifacts; B1 only consumes them.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `control_id` | str | required | Control identifier, e.g. `CP-001-PRE` |
| `session_id` | str or None | None | Bob session the facts are bound to |
| `bob_version` | str or None | None | IBM Bob version that ran the session, e.g. `2.2.0` (selects the lifecycle authority) |
| `configured` | tri | NOT_DETERMINED | Hook for this control present in the applicable Bob config |
| `configured_event` | str or None | None | Lifecycle event the config registers it under |
| `ledger_rows` | int | 0 | Hook-ledger rows for this control bound to this session |
| `ledger_corroborated` | tri | NOT_DETERMINED | Every ledger row's tool-use id joins a tool call in Bob's own record of the same session |
| `absence_witnessed` | bool | False | Bob-side evidence positively shows the hook did not run in this session (e.g. Bob's log says it ignored the hook's matcher) |
| `actual_event` | str or None | None | Lifecycle event the ledger says the control ran at |
| `target_in_payload` | tri | NOT_DETERMINED | The ledger payload shows the control received the governed action (the tool call targeting the protected path) |
| `policy_triggered` | tri | NOT_DETERMINED | The control's own decision for that action was a violation / deny |
| `bob_cancelled_citing_control` | tri | NOT_DETERMINED | Bob's own record shows the governed tool call was cancelled, citing this control |
| `target_exists_after` | tri | NOT_DETERMINED | The after-run workspace snapshot contains the protected target |
| `conflicts` | tuple[Conflict, ...] | `()` | Load-bearing contradictions (detected in task B3) |
| `bundle_verified` | bool | **False** | Evidence bundle verification outcome (task B4). Fail-closed default. |

`has_any_evidence(facts)` is True when `configured` is not NOT_DETERMINED, or `ledger_rows > 0`,
or `absence_witnessed`.

## 4. State derivation

Implement each state as its own function, then `derive_states(facts) -> dict[str, tri]`.

**configured** = `facts.configured`.

**executed** — a hook ledger alone is never sufficient:
- `ledger_rows > 0`: True only if `ledger_corroborated is True`; otherwise NOT_DETERMINED.
- `ledger_rows == 0`: False if `absence_witnessed`; otherwise NOT_DETERMINED.

**observed**: executed False → False; executed NOT_DETERMINED → NOT_DETERMINED; executed True →
`target_in_payload`.

**enforceable** — the load-bearing rule. Implement as

```python
def derive_enforceable(executed, actual_event, authority):
```

with exactly these three parameters. If `executed is True` it returns
`blocking_capable(actual_event, authority)`; otherwise NOT_DETERMINED. It must not read the
policy decision, the cancellation, the after-state, or `enforced`. ENFORCEABLE is never computed
from ENFORCED. `derive_states` passes `authority_for(facts.bob_version)`; an unknown or missing
Bob version therefore gives ENFORCEABLE NOT_DETERMINED.

**enforced** — evaluate in this order, first match wins:
1. executed False → False
2. executed NOT_DETERMINED → NOT_DETERMINED
3. observed False → False; observed NOT_DETERMINED → NOT_DETERMINED
4. `target_exists_after is True` → False (the prohibited action happened)
5. policy_triggered False → False; NOT_DETERMINED → NOT_DETERMINED
6. enforceable False → False (that lifecycle point could not have prevented it); NOT_DETERMINED → NOT_DETERMINED
7. `target_exists_after` NOT_DETERMINED → NOT_DETERMINED
8. `bob_cancelled_citing_control is True` → True; otherwise NOT_DETERMINED (target absent, but Bob does not attribute a block to this control)

## 5. Results

Result codes (records carry codes) and display labels (the UI shows labels):

| Code | Label | Tone | Confident |
|---|---|---|---|
| `CONTROL_ENFORCEMENT_VERIFIED` | ENFORCEMENT VERIFIED | verified | yes |
| `CONTROL_OBSERVATIONAL_ONLY` | OBSERVATIONAL ONLY | partial | yes |
| `CONTROL_CONFIGURED_NOT_EXECUTED` | CONFIGURED — NOT EXECUTED | failed | yes |
| `CONTROL_NOT_CONFIGURED` | NOT CONFIGURED | failed | yes |
| `CONTROL_ACTION_NOT_OBSERVED` | ACTION NOT OBSERVED | partial | yes |
| `CONTROL_NOT_ENFORCEABLE` | NOT ENFORCEABLE | partial | yes |
| `CONTROL_NOT_DETERMINED` | NOT DETERMINED | unknown | no |
| `CONTROL_CONFLICTING_EVIDENCE` | CONFLICTING EVIDENCE | conflict | no |
| `EVIDENCE_NOT_VERIFIED` | EVIDENCE NOT VERIFIED | unknown | no |
| `NO_EVIDENCE` | NO EVIDENCE | unknown | no |

Expose `RESULT_LABELS`, `RESULT_TONE`, and `CONFIDENT_RESULTS` (the six "yes" codes).

`classify(facts, states)` — fail-closed gates first, then a ladder; first match wins:
1. `not facts.bundle_verified` → EVIDENCE_NOT_VERIFIED
2. `not has_any_evidence(facts)` → NO_EVIDENCE
3. `facts.conflicts` non-empty → CONTROL_CONFLICTING_EVIDENCE
4. configured False → NOT_CONFIGURED; not True → NOT_DETERMINED
5. executed False → CONFIGURED_NOT_EXECUTED; not True → NOT_DETERMINED
6. observed False → ACTION_NOT_OBSERVED; not True → NOT_DETERMINED
7. enforceable True and enforced True → ENFORCEMENT_VERIFIED
8. enforceable False → OBSERVATIONAL_ONLY if `policy_triggered is True` and
   `target_exists_after is True`, else NOT_ENFORCEABLE
9. otherwise → NOT_DETERMINED

## 6. Record

`ControlRecord` — frozen dataclass: `control_id`, `session_id`, `states` (dict), `result`,
`label`, `tone`, `confident` (bool), `reason` (one plain sentence for the card face, naming the
missing link or the outcome), `conflicts`, `configured_event`, `actual_event`, `bob_version`,
`lifecycle_authority_source` (the `source` of the authority used, or None when the Bob version
is unknown). `to_dict()` returns a JSON-serializable dict (states via
`state_to_json`, conflicts as dicts).

`evaluate(facts) -> ControlRecord` runs `derive_states` then `classify`.

Reason wording must be evidence-bounded. Required meanings for the three demo results:
- ENFORCEMENT VERIFIED: the control ran at `<actual_event>`, received the prohibited write, and
  Bob cancelled the tool call citing this control.
- OBSERVATIONAL ONLY: the control ran at `<actual_event>`; the prohibited action had already
  occurred, so it could record the violation but could not prevent it.
- CONFIGURED — NOT EXECUTED: the control is present in the Bob configuration but produced no
  execution evidence in this session, so it inspected nothing and blocked nothing.
Do not use the words proven, secure, guaranteed, tamper-proof, or certified anywhere.

## Acceptance tests (`tests/test_model.py`, all required)

Use a helper that builds `ControlFacts` with `bundle_verified=True` and `bob_version="2.2.0"`
unless a test says otherwise. The three demo arms:
- PRE: configured True, configured_event and actual_event `PreToolUse`, ledger_rows 2,
  ledger_corroborated True, target_in_payload True, policy_triggered True,
  bob_cancelled_citing_control True, target_exists_after False.
- POST: same but events `PostToolUse`, bob_cancelled_citing_control False,
  target_exists_after True.
- BADCFG: configured True, configured_event `PreToolUse`, ledger_rows 0, absence_witnessed True,
  target_exists_after True.

1. `test_lifecycle_authority_tables_exact` — `AUTHORITIES` has exactly the keys `2.1.0` and
   `2.2.0`, and each `blocking` table equals the dict in section 2.
2. `test_blocking_capable_unknown_is_not_determined` — under 2.2.0, `None`, `"Notification"` and
   `"PreCompact"` give NOT_DETERMINED; any event with `authority=None` gives NOT_DETERMINED.
3. `test_pre_arm_enforcement_verified` — states all True; result ENFORCEMENT_VERIFIED.
4. `test_post_arm_observational_only` — states (✓ ✓ ✓ ✕ ✕); result OBSERVATIONAL_ONLY.
5. `test_badcfg_arm_configured_not_executed` — configured True, executed False, observed False,
   enforceable NOT_DETERMINED, enforced False; result CONFIGURED_NOT_EXECUTED.
6. `test_enforceable_independent_of_outcome` — for both Bob versions, every event in that
   version's table plus `None` and an unknown event, and every combination of policy_triggered,
   bob_cancelled_citing_control and target_exists_after over {True, False, NOT_DETERMINED}: with
   executed True, enforceable equals `blocking_capable(event, authority)` and is identical across
   all combinations.
7. `test_derive_enforceable_signature` — `inspect.signature(derive_enforceable)` has exactly the
   parameters `executed, actual_event, authority`.
7b. `test_unknown_bob_version_is_not_determined` — PRE arm with `bob_version="9.9.9"` and again
   with `None`: enforceable NOT_DETERMINED, result NOT_DETERMINED, `lifecycle_authority_source`
   None.
8. `test_enforceable_is_not_enforced` — PRE arm with target_exists_after True: enforceable True,
   enforced False, result is not ENFORCEMENT_VERIFIED.
9. `test_ledger_alone_is_not_execution` — ledger_rows 3 with ledger_corroborated NOT_DETERMINED,
   and again with False: executed NOT_DETERMINED, result NOT_DETERMINED.
10. `test_missing_ledger_without_witness_is_not_determined` — BADCFG arm with absence_witnessed
    False: executed NOT_DETERMINED, result NOT_DETERMINED (not CONFIGURED_NOT_EXECUTED).
11. `test_conflict_blocks_confident_result` — PRE arm plus one Conflict: result
    CONFLICTING_EVIDENCE, `confident` False.
12. `test_unverified_bundle_suppresses_result` — PRE arm plus a Conflict with
    bundle_verified False: result EVIDENCE_NOT_VERIFIED (takes precedence over the conflict).
13. `test_bundle_verified_defaults_false` — `evaluate(ControlFacts(control_id="X", configured=True))`
    gives EVIDENCE_NOT_VERIFIED.
14. `test_not_configured` and `test_no_evidence` — configured False → NOT_CONFIGURED; all
    defaults (with bundle_verified True) → NO_EVIDENCE.
15. `test_not_determined_is_never_truthy_or_false` — `bool(NOT_DETERMINED)` raises TypeError;
    `NOT_DETERMINED is not False`; `state_symbol(NOT_DETERMINED) == "—"`.
16. `test_uncertainty_results_not_confident` — CONFLICTING_EVIDENCE, NOT_DETERMINED,
    EVIDENCE_NOT_VERIFIED and NO_EVIDENCE are not in CONFIDENT_RESULTS.
17. `test_record_to_dict_json` — `json.dumps(evaluate(pre).to_dict())` works and a
    NOT_DETERMINED state serializes as `"not_determined"`.
18. `test_wording` — no label, reason, or authority `source` string contains (case
    insensitive) proven, secure, guaranteed, tamper-proof or certified.

## Stop condition

`python -m pytest -q` passes (all tests, including the existing `tests/test_repo_hygiene.py`).
Then commit only the three files above with this message, and stop:

```
bob(B1): core state model, lifecycle authority, independent ENFORCEABLE

Implemented by IBM Bob from bob-tasks/B1-core-state-model.md.
```

Do not push. Do not start another task.
