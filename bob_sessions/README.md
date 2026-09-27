# IBM Bob task sessions

Screenshots are taken in Bob IDE -> Tasks -> task header -> task session consumption summary,
and saved here as PNG. File names give the **IBM Bob task number** first and the engineering
phase(s) that task contained second:

    controlproof_taskNN_<phase>-<short-description>_summary.png

Cost is the value Bob shows on the task itself (the coin figure in the task header). Bob meters
tasks, not engineering phases.

## Bob Task 1 — engineering phases B1 and B2

B1 was started as a new Bob task. B2 was then sent as a follow-up message **inside the same Bob
task** instead of using New Task — an operator slip, not a design choice. Bob therefore metered
B1 and B2 together, and B2 was never metered on its own. No attempt is made here to present B2
as an independently metered Bob task.

| Phase | Spec | Commit | Bob cost | Screenshot |
|---|---|---|---|---|
| B1 core state model | [B1](../bob-tasks/B1-core-state-model.md) | `a4654fe` | 0.850 — task total at the B1 checkpoint | [B1 checkpoint](controlproof_task01_b1_checkpoint_summary.png) |
| B2 evidence ingestion | [B2](../bob-tasks/B2-evidence-ingestion.md) | `66eea1a` | ≈ 3.430 — approximate increment (4.28 − 0.850), not a separate meter reading | — |
| **Bob Task 1 total (B1 + B2)** | | | **4.28** | [B1 + B2 combined](controlproof_task01_b1-b2_combined_summary.png) |

## Later Bob tasks

From B3 on, each engineering phase is started with New Task in Bob and is metered on its own.

| Bob task | Phase | Spec | Commit | Bob cost | Screenshot |
|---|---|---|---|---|---|
| Task 2 | B3 hook + conflict detection | [B3](../bob-tasks/B3-hook-and-conflicts.md) | `5babfd8` | 5.18 | [task02](controlproof_task02_b3-conflicts_summary.png) |
| Task 3 | B4 final build (bundle, capture, receipt, judge page) | [B4](../bob-tasks/B4-final-build.md) | `1cebbbb` | 5.16 | [task03](controlproof_task03_b4-final-build_summary.png) |
