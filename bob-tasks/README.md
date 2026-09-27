# IBM Bob task protocol

Each file in this folder is the complete specification for exactly one IBM Bob task.

Rules for every task:

1. One Bob task at a time, in Bob IDE Agent mode, with this repository open as the workspace.
2. The task file names the only files Bob may create or change. Everything else is out of scope.
3. The task is done when every acceptance test in the task file passes and the full suite
   (`python -m pytest -q`) is green. Tests are never weakened, skipped or deleted to get green.
4. Bob commits once, with the message given in the task file, then stops. Bob does not push and
   does not start the next task.
5. If something in the task file is ambiguous, Bob chooses the reading that is more conservative
   about what the evidence shows (NOT DETERMINED over a confident state) and notes it in the
   commit body.

Prompt used to start a task (typed into the Bob Agent panel):

    Read bob-tasks/<TASK FILE> in this repository and perform exactly the task it describes.
    Stay inside the files it lists. Run `python -m pytest -q` until it passes, then commit with
    the message given in the task file and stop.

| Task | File | Scope |
|---|---|---|
| B1 | [B1-core-state-model.md](B1-core-state-model.md) | Evidence schema, five states, lifecycle authority, independent ENFORCEABLE, result ladder |
| B2 | [B2-evidence-ingestion.md](B2-evidence-ingestion.md) | Evidence ingestion, session binding, tool-use id correlation, corroborated EXECUTED, model input hardening |
| B3 | [B3-hook-and-conflicts.md](B3-hook-and-conflicts.md) | ControlProof hook, conflict detection (10 codes), B2 carry-over fixes |
| B4 | [B4-final-build.md](B4-final-build.md) | Evidence bundle + manifest/pin, provision + capture, receipt, static judge page, CLI (final build) |
