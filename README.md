# ControlProof

**Same policy. Same action. Three different realities.**

ControlProof provides session-bound runtime evidence showing whether a specific IBM Bob control
was configured, executed, exposed to the intended action, had the expected enforcement authority,
and enforced the tested policy.

> **Status: in development during the IBM Bob 2.0 Hackathon (lablab.ai, 25–27 Sep 2026).**
> This README describes the goal and how the project is being built. Sections describing working
> functionality are added only once the code and its tests exist in this repository.

## The developer workflow: Bob guardrail verification before merge

A team adds a guardrail to IBM Bob — for example a hook that must stop Bob from writing under
`protected/`. Before that change is merged, someone has to answer: *did the guardrail actually
stop the action, or did it only notice it afterwards, or did it never run at all?*

Today that means reading, by hand and in the right order:

- `.bob/settings.json` and the hook configuration,
- which lifecycle point the hook is registered at, and whether that point can block anything,
- the Bob task transcript,
- the hook's own ledger,
- Bob's runtime log,
- the before/after state of the workspace.

ControlProof reads the same artifacts and produces one evidence-derived receipt per control.
Each control gets five separate states, never collapsed into pass/fail:

| State | Meaning |
|---|---|
| CONFIGURED | The control exists in the applicable IBM Bob configuration. |
| EXECUTED | The control ran in this Bob session, and its own report is corroborated by Bob's record of the session. |
| OBSERVED | The control received the action it governs. |
| ENFORCEABLE | The lifecycle point it ran at is capable of preventing that action (from IBM Bob's lifecycle semantics, never from the outcome). |
| ENFORCED | The control triggered, the prohibited action did not happen, and the outcome is attributable to this control. |

When the evidence does not support a confident answer, ControlProof says so instead of guessing:
**CONFLICTING EVIDENCE**, **NOT DETERMINED**, or **EVIDENCE NOT VERIFIED**.

## How this project is being built (timeline and provenance)

**Before the event (Sep 2026, not in this repository).** The author did private research on IBM
Bob's hook lifecycle: which lifecycle events can block a tool call, and how a hook's own
execution report can be checked against Bob's record of the same session. This consisted of a
research spike (`SPIKE-BOB-CONTROLPROOF-001`), a small prototype (`IBM-BOB-CONTROLPROOF-MVP`) and
its independent audit, a rehearsal of the event plan (`CONTROLPROOF-EVENT-REHEARSAL-001`), and
earlier experiments on AI-development controls (`ADCF`, `EXP-ADCF-003`). That material is used
during the event only as reference for writing task specifications. **None of its code is copied
into this repository.**

**Event window (from 2026-09-25 08:00 PDT).** This repository was created empty at 08:53 PDT on
25 Sep 2026.

- Scaffolding and the task specifications in [`bob-tasks/`](bob-tasks/) are written with
  **Claude Code** (Anthropic) acting as the engineering assistant. Those commits carry a
  `Co-Authored-By: Claude` trailer.
- The product code is implemented by **IBM Bob** (Bob IDE, Agent mode), one task file per Bob
  task, one commit per task. Bob task commits begin with `bob(Bn):`.
- Each Bob task's session consumption summary is saved in [`bob_sessions/`](bob_sessions/).

## Repository layout

| Path | Contents |
|---|---|
| `bob-tasks/` | One specification per IBM Bob task: objective, files in scope, acceptance tests, stop condition. |
| `bob_sessions/` | IBM Bob task session summary screenshots and the Bob budget log. |
| `tests/` | Test suite (`python -m pytest -q`). |

## License

MIT — see [LICENSE](LICENSE).
