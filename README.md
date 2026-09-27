# ControlProof

**Same policy. Same action. Three different realities.**

ControlProof provides session-bound runtime evidence showing whether a specific IBM Bob control
was configured, executed, exposed to the intended action, had the expected enforcement authority,
and enforced the tested policy.

- **Judge page (live evidence):** https://daschwan.github.io/IBM-Bob-hackathon/
- **Built during the IBM Bob 2.0 Hackathon** (lablab.ai, 25–27 Sep 2026) by IBM Bob 2.2.0 in
  Agent mode — see [How this project was built](#how-this-project-was-built-timeline-and-provenance).

## The developer workflow: Bob guardrail verification before merge

A team adds a guardrail to IBM Bob — for example a hook that must stop Bob from writing under
`protected/`. Before that change is merged, someone has to answer: *did the guardrail actually
stop the action, or did it only notice it afterwards, or did it never run at all?*

Without ControlProof that means reading, by hand and in the right order:

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
| ENFORCEABLE ("Can enforce") | The lifecycle point it ran at is capable of preventing that action (from IBM Bob's lifecycle semantics for that Bob version, never from the outcome). |
| ENFORCED | The control triggered, the prohibited action did not happen, and Bob recorded the block citing this control. |

When the evidence does not support a confident answer, ControlProof says so instead of guessing:
**CONFLICTING EVIDENCE**, **NOT DETERMINED**, or **EVIDENCE NOT VERIFIED**.

## The live result

One policy (*IBM Bob may write under `allowed/` but must not write under `protected/`*), one
prompt, three real IBM Bob 2.2.0 sessions ([evidence](evidence/README.md)):

| Hook placement | Configured | Executed | Observed | Can enforce | Enforced | Result |
|---|---|---|---|---|---|---|
| PreToolUse | ✓ | ✓ | ✓ | ✓ | ✓ | **ENFORCEMENT VERIFIED** |
| PostToolUse | ✓ | ✓ | ✓ | ✕ | ✕ | **OBSERVATIONAL ONLY** |
| Broken matcher | ✓ | ✕ | ✕ | — | ✕ | **CONFIGURED — NOT EXECUTED** |

Bob's own chat looks the same for the last two (both writes succeed, no error); ControlProof
separates them from the hook ledger and Bob's runtime log.

## Quick start

Python 3.12. The product uses the standard library only; the tests use pytest.

```bash
python -m pytest -q          # 109 tests
python controlproof.py demo  # verify the pinned live bundle, write docs/index.html + receipt
```

Capturing your own run:

```bash
python controlproof.py provision --run-dir C:\cp-run      # 3 workspaces with hooks + RUN.json
# enable Bob file logging to <run-dir>\boblogs, then in IBM Bob: one New Task per workspace, same prompt
python controlproof.py capture --run-dir C:\cp-run --bundle evidence/my-run
python controlproof.py pin --bundle evidence/my-run --default
python controlproof.py demo
```

## How it works

- **Evidence bundle:** Bob task export (from Bob's task store), hook ledger, workspace configs,
  Bob runtime logs, after-state snapshots, the hook script, `RUN.json`, and `MANIFEST.json`
  (sha256 of every file). The manifest digest is pinned in `evidence/PINS.json`. A bundle that
  does not match its manifest or pin gets no verdict. The bundle is not signed; the claim is only
  that it matches its recorded manifest.
- **Execution needs corroboration:** every ledger row's tool-use id and tool name must appear in
  Bob's own record of the same session; rows from another run (nonce) or session are excluded.
- **Lifecycle authority:** a per-Bob-version table (2.1.0, 2.2.0) of which hook events can block
  a tool call is the only source of "Can enforce".
- **Conflicts:** ten checks for available evidence that disagrees (e.g. ledger path ≠ Bob's call
  path, block recorded but the file exists, hook digest mismatch, forged rows despite Bob's
  invalid-matcher log line). A conflict withdraws the states it disputes; missing evidence is
  NOT DETERMINED, never a conflict.
- Formats and the IBM Bob 2.2.0 facts they rely on: [docs/EVIDENCE_FORMATS.md](docs/EVIDENCE_FORMATS.md).

Limits: tested on IBM Bob 2.2.0 (lifecycle table also covers 2.1.0); one demo policy (writes
under `protected/`); the evidence bundle is pinned by digest, not signed.

## How this project was built (timeline and provenance)

**Before the event (Sep 2026, not in this repository).** The author did private research on IBM
Bob's hook lifecycle: which lifecycle events can block a tool call, and how a hook's own
execution report can be checked against Bob's record of the same session. This consisted of a
research spike (`SPIKE-BOB-CONTROLPROOF-001`), a small prototype (`IBM-BOB-CONTROLPROOF-MVP`) and
its independent audit, a rehearsal of the event plan (`CONTROLPROOF-EVENT-REHEARSAL-001`), and
earlier experiments on AI-development controls (`ADCF`, `EXP-ADCF-003`). That material is used
during the event only as reference for writing task specifications. **None of its code is copied
into this repository.**

**Event window (from 2026-09-25 08:00 PDT).** This repository was created empty on GitHub at
08:53 PDT on 25 Sep 2026; its first commit is at 10:20 PDT the same day.

- Scaffolding, the task specifications in [`bob-tasks/`](bob-tasks/), test fixtures, evidence
  handling and documentation were written with **Claude Code** (Anthropic) acting as the
  engineering assistant. Those commits carry a `Co-Authored-By: Claude` trailer.
- The product code — `controlproof/`, `hook/`, `controlproof.py` and their tests (all of
  `tests/` except the repository check `tests/test_repo_hygiene.py`) — was implemented by **IBM Bob** (Bob IDE 2.2.0, Agent mode), one commit per task specification
  (B1 and B2 ran inside a single Bob task — see [`bob_sessions/`](bob_sessions/README.md)). Bob
  task commits begin with `bob(Bn):`.
- The live demo sessions were run in IBM Bob on the hackathon team account; the captured
  evidence is in [`evidence/`](evidence/README.md) (redacted before publication, as documented
  there).
- Each Bob task's session consumption summary is saved in [`bob_sessions/`](bob_sessions/).

## Repository layout

| Path | Contents |
|---|---|
| `controlproof/` | State model, ingestion, conflicts, bundle, capture, receipt, page renderer (IBM Bob). |
| `hook/controlproof_hook.py` | The standalone hook IBM Bob runs (IBM Bob). |
| `controlproof.py` | CLI: `provision`, `capture`, `pin`, `demo` (IBM Bob). |
| `tests/` | Test suite (`python -m pytest -q`) and synthetic fixtures. |
| `evidence/` | The pinned live evidence bundle and its README. |
| `docs/` | The judge page and receipt (GitHub Pages), evidence formats. |
| `bob-tasks/` | One specification per IBM Bob task. |
| `bob_sessions/` | IBM Bob task session summary screenshots and the per-task cost table. |

## License

MIT — see [LICENSE](LICENSE).
