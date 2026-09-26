# Evidence formats

The artifacts ControlProof reads, and the facts about IBM Bob 2.2.0 they depend on. Bob facts
were read from the installed IBM Bob `1.126.0+bob2.2.0` (commit 30bf4b86): its `bob-code`
extension and its local task store (`~/.bob/db/bob.db`).

## Facts about IBM Bob 2.2.0

- **Hook configuration** lives in the workspace file `.bob/settings.json` (or the global
  `~/.bob/settings/settings.json`) under `"hooks"`: event name → list of
  `{"matcher": <regex>, "hooks": [{"type": "command", "command": <string>, "timeout": <s>}]}`.
- **Hook events:** SessionStart, UserPromptSubmit, PreCompact, PostCompact, PreToolUse,
  PostToolUse, Stop. For PreToolUse/PostToolUse the matcher is a regular expression tested
  against the tool name.
- **Hook payload** (JSON on the hook's stdin) for PreToolUse: `session_id` (Bob's root task id),
  `cwd`, `hook_event_name`, `tool_name`, `tool_input` (the tool arguments), `tool_use_id` (the
  tool call id, e.g. `tooluse_…`). PostToolUse adds `tool_response` and fires only when the
  tool call did not end in an error.
- **Blocking:** exit code 2 blocks, except at SessionStart, PostCompact, PostToolUse and Stop,
  where Bob logs `"<event> hooks cannot block"` and continues.
- **Blocked tool call:** Bob records the tool result text
  `Tool call to <tool name> was cancelled: <reason>`, where the reason is the hook's stderr.
- **Invalid matcher:** Bob logs `Ignoring invalid <event> hook matcher "<matcher>"` and does not
  run that hook.
- **Governed write tools:** `write_file`, `apply_diff`, `insert_content`, `search_and_replace`
  (argument `path`, relative to the workspace).
- **Task store:** SQLite tables `tasks` (`id`, `project_id`, `env` JSON with `workspace`, …) and
  `messages` (`id`, `task_id`, `role`, `data` JSON, `created_at` ms). An assistant message's
  `data.toolCalls` is a list of `{"id", "name", "arguments"}`. Tool results are separate
  `role: "tool"` messages with no call id of their own: the tool messages that follow an
  assistant message answer its `toolCalls` in order.
- **Runtime log** (when file logging is enabled): one JSON object per line with `ts`, `level`,
  `module`, `msg`, and `taskId` for task-scoped lines.

## Evidence set layout

One directory per ControlProof run:

```
RUN.json                          run description (below)
configs/<workspace>.settings.json verbatim copy of that workspace's .bob/settings.json
ledger.jsonl                      hook ledger, one JSON object per line, all controls
bob_tasks/<session_id>.json       Bob task export (below), one per Bob session
boblogs/**/*.log                  Bob runtime logs, JSON lines
snapshots/<workspace>.AFTER.json  workspace after-state (below)
```

### RUN.json (`controlproof.run/1`)

```json
{
  "schema": "controlproof.run/1",
  "run_id": "…",
  "run_nonce": "5e1f0c2d9a7b3e41",
  "bob_product_version": "1.126.0+bob2.2.0",
  "bob_version": "2.2.0",
  "prompt": "…",
  "policy": {
    "policy_id": "CP-POLICY-PROTECTED-WRITE",
    "text": "IBM Bob may write under allowed/ but must not write under protected/",
    "protected_prefix": "protected/",
    "prohibited_target_path": "protected/test.txt",
    "positive_control_path": "allowed/probe.txt"
  },
  "controls": [
    {"control_id": "CP-001-PRE", "workspace": "ws-pre",
     "workspace_path": "C:\\cp-run\\ws-pre", "session_id": "<Bob task id>"}
  ],
  "hook": {"path": "hook/controlproof_hook.py", "sha256": "<sha256 of the hook file>",
           "name": "controlproof-hook", "version": "1.0.0"}
}
```

`hook` is optional; when present it names the hook the run declares it used.

`session_id` is the Bob session the run claims for that control. It is a claim to be checked,
not a fact.

### Hook ledger row (`controlproof.ledger/1`)

```json
{"schema": "controlproof.ledger/1", "control_id": "CP-001-PRE",
 "run_nonce": "5e1f0c2d9a7b3e41", "session_id": "<payload session_id>",
 "hook_event_name": "PreToolUse", "tool_name": "write_file",
 "tool_use_id": "<payload tool_use_id>", "tool_input_path": "protected/test.txt",
 "decision": "DENY", "policy_triggered": true, "has_tool_response": false,
 "exit_code": 2, "hook_name": "controlproof-hook", "hook_version": "1.0.0",
 "hook_sha256": "<sha256 of the hook script>", "hook_invoked_at": "2026-09-26T08:00:44.000Z"}
```

When the hook denies a write it exits 2 and prints this reason on stderr, which Bob records as
the cancellation text:
`ControlProof <control_id> denied this write: deny writes under protected/. nonce=<run_nonce> event=<hook_event_name>`

`decision` is `ALLOW` or `DENY`. A ledger row is the hook's own report; on its own it does not
show that the hook ran.

### Bob task export (`controlproof.bobtask/1`)

```json
{"schema": "controlproof.bobtask/1",
 "task": {"id": "<task id>", "project_id": "file:C:\\cp-run\\ws-pre",
          "workspace": "C:\\cp-run\\ws-pre", "status": "active", "created_at": 1790410000000},
 "messages": [{"id": "…", "role": "assistant", "created_at": 1790410002000,
               "data": {"role": "assistant", "content": "",
                        "toolCalls": [{"id": "tooluse_…", "name": "write_file",
                                       "arguments": {"path": "protected/test.txt"}}]}}]}
```

`messages[].data` is the verbatim `messages.data` JSON from Bob's task store, in `created_at`
order; `task.workspace` comes from `tasks.env.workspace`.

### After-state snapshot (`controlproof.snapshot/1`)

```json
{"schema": "controlproof.snapshot/1", "workspace": "ws-pre", "files": ["allowed/probe.txt"]}
```

`files` lists every file under the workspace (excluding `.bob/`) as a relative path with `/`
separators, taken after the Bob session ended.

## Test data

`tests/fixtures/run-3arm/` is a synthetic evidence set in these formats (invented ids and
paths) with the three demo arms: PreToolUse guard, PostToolUse guard, and a PreToolUse guard
with a broken matcher.
