# Architecture

`ui.py` is a PySide6 client. Work goes through a loopback JSON RPC broker in a detached
process. The UI may close while the broker and external terminal helpers remain alive.
`instance.py` holds a kernel file lock per canonical data directory and a local Qt activation
endpoint. Duplicate UI launches activate the owner and exit; a crash releases the lock.
The broker has a separate lock and runs under the console helper in Windows packages.
`store.py` owns SQLite transactions, sessions, credentials, direct pairs, durable messages,
and settings. Policy changes and claims serialize; once a provider request is in flight,
disabling communication cannot retract it. Toggle completion waits for that delivery attempt.

`providers.py` attaches to the existing Codex daemon using `codex app-server proxy` and uses
the generated protocol's thread ID, not its shared tree session ID. The connection is only
a proxy: closing it does not stop the daemon. Approval requests are never auto-approved.
Discovery and status require a working shared daemon; live metadata attachment now passes.
Resume and queue explicitly target `unix://`, matching the shared daemon connection.
Operator new/resume first starts the shared daemon if absent using `app-server daemon start`
and verifies an initialized proxy handshake before launch. Background discovery never starts
or restarts a daemon. Failed startup creates no terminal ticket or managed-window association.
There is no automatic private-database fallback.

`claude.py` supplies saved-session discovery and a version-gated `claude agents --json --all`
live roster. It uses full session UUIDs, never short background-job IDs as conversation IDs.
Saved transcript metadata is bounded/read-only and never proves runtime state. The live
roster currently requires 2.1.290+; older versions use the history fallback and managed hooks.
Each launch receives scoped `--settings` hooks and `--mcp-config`, with exact UUID new/resume
flags. Hooks observe lifecycle/permission/input events, reject switched session IDs and never
return decisions or permission verdicts. Stop events retain working state when reported
background tasks or scheduled wakeups remain.

Claude messaging supports inbox, explicitly opted-in local development channel, and an
installed/approved plugin. Mode selection applies to the next launch; it does not alter
running sessions or global Claude settings. Local preview leaves Claude's confirmation and
organization policy intact. The plugin payload is bundled for marketplace distribution;
public/organization channel allowlisting is external to VISOR. No second print-mode worker
is started to inject messages into an active session. Live Claude model testing is waived.

`terminal.py` runs in each deliberately visible independent terminal. It holds the window
open after the provider exits and heartbeats independently of the UI. Launch tickets are
one-shot local files; command arguments are passed as arrays. No provider/user message is
interpolated into an operating-system shell command. macOS's unavoidable Terminal shell
launch quotes each helper argument and passes it to AppleScript as argv.

`platforms.py` locates unique launch titles and combines them with fresh helper heartbeats
before window actions. Windows additionally checks the helper's executable and native process
creation time, then pins the terminal HWND, owning PID, executable and creation time. Reused
process IDs or changed window identities fail closed. Actual Windows Terminal association,
heartbeat, launch and Focus have passed local/operator checks. Operator association of
externally launched windows remains outstanding. Windows resume recovers stale ownership only
when the terminal is absent and the recorded helper is proven gone. Access denied, ambiguous
windows, and live helpers block recovery. Other cases require explicit closed-terminal
reconciliation. Reopening a managed terminal attaches to the shared Codex thread even when
that thread is still loaded; it does not start a second independent agent.

Helper credentials are distinct from agent credentials: only helpers report heartbeats and
process exit, and helpers cannot send messages. These API roles do not create a security
boundary against arbitrary file reads by other processes running under the same OS account.

`mcp.py` offers peers, send, receive, acknowledge, the typed operator_reply described below, and scoped Prism activity tools. Sender identity comes from the
credential. A session can have multiple approved peers; no implicit/transitive connection
exists. Messages remain queued/held until delivery, with explicit acknowledgment/completion.
Crashes during sending become delivery_unknown and are not automatically retried. MCP delivery
stays sending until the transport reports writing its response; unconfirmed attempts time out
to delivery_unknown. An acknowledgment may arrive first and is never downgraded by a late
transport receipt. Pending inboxes are bounded to 500 messages per recipient.

For opted-in Claude channels, initialize advertises `experimental["claude/channel"]` and
does not advertise permission relay. After the client's initialized notification, a scoped
worker claims permitted messages and emits `notifications/claude/channel`. Response and
notification writes share a lock. A channel write remains delivery-unknown because Claude
does not acknowledge notifications and can silently discard blocked channels. Explicit
recipient acknowledgment/completion is required; late write receipts cannot downgrade it.
The durable queue and credential reconnect to a restarted broker without automatic retries
of uncertain messages. Real subprocess tests cover this with a simulated Claude client.

The frameless supervisor uses Windows WM_NCHITTEST edge/corner values and a Qt event filter
for child-widget edge presses on other platforms, plus a corner grip. Default-size restore
is independent of edge resizing. Native drag/DPI acceptance remains distinct from unit tests.

The database has a schema version and an additive migration for helper roles and Windows
identity fields. Newer unsupported schema versions are rejected. Shutdown tracking persists
its target set; cancelling tracking never retracts delivered wrap-up requests or releases held
messages. Closed sessions are not included in new wrap-up requests.



## Permanent session deletion

The operator-only `delete_session` request requires `confirmed: true`. Launch and delivery locks serialize it with terminal launches and message claims. Codex descendant discovery includes active and archived records and verifies parent ancestry locally before taking action. Each known target's linked managed window is verified and closed without a graceful agent request; Windows additionally waits for its pinned helper process to retire. The shared terminal host and Codex daemon are never killed. Missing/ambiguous live associations or a close timeout abort provider deletion.

Provider history deletion precedes registry cleanup: Codex uses `thread/delete`; Claude removes only canonical-UUID conversation transcripts and session artifact directories within its resolved configuration root. Every recursive target is checked before mutation. Provider/close errors preserve the VISOR row for retry; a terminal may already have closed before a later failure. After success, SQLite removes related pairs, credentials, messages, runtime/layout/shutdown entries and the session row. A separate identity-only tombstone prevents an in-flight discovery from re-inserting the conversation. There is no restore action for permanent deletion. This does not erase project source files or promise cloud/shared-log/backup removal.

## Human operator messages and final replies

Operator `ping` advertises `capabilities: ["message_get", "operator_reply"]` and
`limits: {"message_max_chars": 16000, "max_rpc_bytes": 65536}`. A browser facade
must check these before exposing this communication workflow. An old/unavailable
broker is a capability hold, not permission to infer receipts from `snapshot`.
The snapshot contains only the latest 200 VISOR messages globally; it is partial
history, separate from provider conversations.

`message_get {message}` is operator-only and returns the exact raw VISOR message
with `reply: null` or its attached typed reply. The trusted operator facade must
verify its authenticated user's session/event authorization and emit a filtered
DTO; raw message fields include internal detail and deduplication data. Deleted,
cleared and missing events are rejected. Agent, helper and Prism-only credentials
cannot perform this lookup.

`operator_reply {message, body, request_id}` is agent-only. The authenticated agent
must be the original recipient of a regular `sender=operator`, `origin=operator`,
`kind=message` event. Wrap-up and agent-origin messages are ineligible. The parent
must be sending, delivered, delivery_unknown, acknowledged or completed; allowing
sending covers a recipient responding before the transport receipt arrives. The
reply's fixed destination is the human operator. No synthetic operator session,
peer connection, participation or master-policy bypass is added to agent send.

One immutable final reply is stored under `settings["operator_reply:" + parent_id]`
in the existing SQLite database, without adding a table or message column. The
record has `schema="visor.operator_reply.v1"`, `id`, `parent_message_id`,
`parent_sha256`, authenticated `sender`, `recipient="operator"`, canonical `body`,
`request_id`, and `created` (epoch seconds). The reply operation returns this same
record. Parent/request/reply IDs are canonical lower-case hyphenated UUIDs. Body
validation uses Python `str.strip()`, 1–16000 Unicode code points, and strict UTF-8;
U+001C through U+001F are stripped, whereas U+FEFF is retained. Mutable transport
state is excluded from the parent hash: SHA-256 covers UTF-8 JSON of precisely
id/sender/recipient/origin/kind/body, sorted keys, compact separators, unescaped
Unicode. Parent and reply are read in one SQLite snapshot. An immediate write
transaction checks binding and uses INSERT once; retrying the same parent, sender,
request and canonical body returns the prior record. Any different final reply
conflicts. Subsequent conversation requires another operator message.

Posting does not acknowledge reading, report completion, approve action or prove
execution. Existing `acknowledge` and `completed` semantics remain independent.
The `operator_reply:` namespace is rejected by all three generic settings helpers.
Only typed bound-SQL methods read/write it; snapshot and configuration routes do
not expose it. Malformed records, changed parent hashes and orphan references fail
closed on typed reads and retries.

Message deletion, session deletion and completed/failed retention clearing remove
attached reply content atomically with the parent lifecycle change. Pending and
uncertain deliveries are retained. Private SQLite backups include replies and their
parents; restore invalidates credentials and retains binding checks before reads.
Clearing live content does not erase retained backups. Supported rollback disables
the new browser capabilities while retaining this forward-compatible Store cleanup.
An arbitrary older Store would not know these keys; downgrading it requires an
explicit reply-preservation and retention plan before resuming operations.
