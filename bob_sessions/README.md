# IBM Bob task sessions

One entry per IBM Bob task. Screenshots are taken in Bob IDE -> Tasks -> task header ->
task session consumption summary, and saved here as PNG:

    controlproof_taskNN_<short-description>_summary.png

Task cost is the value Bob shows on the task itself (the coin figure in the task header).

## Task log

| Task | Spec | Bob task cost | Commit | Screenshot |
|---|---|---|---|---|
| B1 core state model | [B1](../bob-tasks/B1-core-state-model.md) | 0.850 | `a4654fe` | [task01](controlproof_task01_core-state-model_summary.png) |
| B2 evidence ingestion | [B2](../bob-tasks/B2-evidence-ingestion.md) | 3.430 (see note) | `66eea1a` | [task02](controlproof_task02_evidence-ingestion_summary.png) |

Note on B2: B2 was sent as a follow-up message inside the same Bob task as B1, so the task header in the task02 screenshot shows the combined total for B1 and B2 (4.28). B2's share is 4.28 − 0.850 (B1's total when B1 ended) = 3.430. Later tasks each run in their own Bob task.
