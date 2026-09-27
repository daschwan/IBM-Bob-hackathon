# Synthetic three-arm evidence set — IBM Bob 2.2.0 stored form

Same invented run as `../run-3arm/`, but the Bob task exports use the form IBM Bob 2.2.0 writes
to its task store: every tool result carries `toolUsage.signature` (`id`, `name`, `arguments`,
`isError`), and a call blocked by a hook is stored with `isError: true` and the hook's reason as
the whole content (no `Tool call to <name> was cancelled: ` prefix). Shape taken from the live
2026-09-27 capture; all ids, paths and timestamps here are invented. Not captured evidence.
