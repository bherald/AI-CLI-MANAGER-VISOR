# Windows functional audit — 2026-10-19

This records the earlier `wrap-up-audit-1` build. The subsequent Claude implementation,
waived live-model testing policy, and edge-resize correction are recorded in current
[STATUS.md](STATUS.md) and [README.md](../README.md); the old Claude gaps below are historical.

Build: `2026-10-19-wrap-up-audit-1`. Existing user sessions and history are preserved.

| Function | Evidence / result |
| --- | --- |
| Session discovery | Live shared Codex server returns interactive sessions, including CLI, editor, exec, app-server, and unknown source kinds. Existing records were retained. |
| Filtering | Tests cover search/provider/managed-only filters and Show all. UI shows visible/total counts. The old Open only label incorrectly implied it included unmanaged Codex terminals; it now says Managed terminals only. |
| Session menus | Reproduced QMenu deletion by an in-flight periodic refresh. Regression passes after deferring reconstruction while a popup is open. Wrap up targets the selected row exactly once. |
| Launch/resume and Focus | Operator confirmed opening another session and successful Focus. The managed terminal had a verified Windows Terminal identity and helper heartbeat. |
| Identity/restart recovery | Tests cover missing windows, ambiguous windows, live/dead helpers, PID reuse, and recovery after broker restart. |
| Operator messaging | Passed against both source and the packaged EXE: live disposable Codex thread received a real queued message and explicitly confirmed completion. |
| Wrap up | Passed against both source and the packaged EXE: the live thread received Wrap up, explicitly confirmed completion, and VISOR recorded confirmed shutdown. Test threads archived afterward. No user project was shut down by the audit. |
| Acknowledgment integrity | Only an exact receipt in an agent final reply following the exact VISOR envelope in the same turn is accepted. Completion additionally requires a completed turn. Tests reject ordinary prose, quoted/fenced text, commentary, unrelated IDs, and unrelated user input. |
| Multiple shutdown targets | Separate requests retain previously tracked targets. Zero reachable targets reports failure rather than returning empty success. |
| Agent pairs and message policy | Automated tests cover direct/non-transitive pairs, scoped identities, operator independence, held messages, duplicate requests, rates, and queue limits. This is not a live multi-agent exchange test. |
| Single supervisor | Real subprocess and packaged duplicate-instance tests. |
| Packaging | Native build plus extracted package smoke, broker restart, durable queue, checksum, and singleton acceptance. |
| Window controls/layout | Automated UI/layout tests; operator-confirmed normal Focus. Native minimized/maximized restoration and multiple-monitor scenarios remain unconfirmed. |
| Claude Code | Isolated live inbox/acknowledgment test attempted. The CLI returned HTTP 401 invalid OAuth access token before any model work. Adapter tests pass; live Claude push/status and cross-provider acceptance remain blocked/unverified. |
| macOS/Linux | Not tested on native machines in this Windows audit. |

## Root cause of the incomplete Wrap up indication

The existing agent received the request and reported completion, but could not call VISOR
acknowledge because its thread did not have the VISOR MCP tools. Thread-scoped tool discovery
returned no VISOR server. An isolated experiment established that supplying the configuration
at thread/start (or after unloading a test thread) loaded the tools; supplying it while resuming
an already-loaded thread did not. User threads were not unloaded to work around this.

New Codex deliveries include an explicit reply receipt fallback. The broker reads the newest
50 turns of each pending recipient every refresh cycle and accepts only the exact bound
receipt. MCP acknowledgment remains available where attached. Ordinary completion prose from
an older request is not retroactively treated as an acknowledgment. Agent-confirmed completion
still does not prove all background work has stopped.

The fallback makes operator delivery/completion work on already-loaded Codex sessions. It does
not install the missing MCP tools or make peer messaging available in those sessions. That
attachment gap and live cross-provider validation remain release gates.

## Reproduction commands

`python scripts/check.py` — isolated automated suite.

`python scripts/verify_codex_delivery.py` — explicitly live acceptance: creates one disposable
Codex thread, sends a harmless message and Wrap up, verifies completion, and archives the thread.
Requires an authenticated shared Codex server; uses the account's normal model settings.

`python scripts/verify_windows_package.py --archive <zip>` — isolated package acceptance.

`python scripts/verify_single_instance.py <visor.exe>` — packaged singleton acceptance.

`python scripts/verify_claude_mcp.py` — live cooperative inbox/acknowledgment test using an
isolated broker. Requires a valid Claude login. The 2026-10-19 attempt failed authentication;
neither queued test message was delivered, and the isolated broker was stopped.

Official protocol reference: [Codex App Server](https://learn.chatgpt.com/docs/app-server).
