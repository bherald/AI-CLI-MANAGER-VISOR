# Security and privacy

VISOR is a beta local application under evaluation. Do not expose its broker to a network.
The broker binds to 127.0.0.1, requires a random bearer credential, rejects browser Origin
requests, limits request sizes, and separates operator controls from session-scoped MCP tools.
Pair authorization is explicit and non-transitive. Agents cannot choose a sender identity in
the API, enable communication, add pairs, launch sessions, or stop the broker.

The current trust boundary is the operating-system user account. Processes with unrestricted
read access to that account's application-data directory can read its credentials. Session
tokens are API scoping, not isolation against a malicious process running as the same user.
Do not grant agents access to VISOR's private data directory. Portable directories must have
appropriate OS access controls. New Windows data directories receive a private DACL for the current
account, SYSTEM and Administrators. Existing directories are inspected without changing their permissions;
broader grants are rejected. Network filesystems and unusual ACLs still need native acceptance.

Provider credentials remain provider-managed. Messages and application state are stored
locally and are not encrypted at rest. Use OS disk encryption if needed. Clear-history removes
completed/failed message text, retaining idempotency records; queued, held, delivered-but-not-
completed, and ambiguous messages are retained. Offline `--maintain-storage --retention-days 30 --compact`
clears old completed/failed message text and compacts storage while retaining deduplication records.
Close the broker first. This is not secure erasure and does not remove provider history or backups.

VISOR does not bypass provider approvals and does not execute message text as a shell command.
Graceful wrap-up never implies permission to force-terminate processes. No telemetry is added.

The maintainer is [bherald](https://github.com/bherald). For a published repository, use its Security tab and **Report a vulnerability** when private vulnerability reporting is enabled. Private reporting availability depends on the repository settings; verify the Security tab before submitting. Do not disclose a vulnerability containing credentials or private transcripts through a public issue or profile comment. Enabling and verifying private reporting is a repository-publication step.

State snapshots remain private and can retain messages deleted after a backup. Automatic pre-upgrade backups are opt-in with bounded retention. Offline recovery invalidates old scoped credentials and terminal links and holds pending delivery. Treat exported/copied snapshots as sensitive; no provider login or provider history is included.

## Optional browser access

VISOR 1.5.0 can explicitly start a separate VISOR/Prism browser server. It is off
by default. The UI defaults to loopback; an operator can choose a specific
LAN/VPN IP. Stop revokes its private link, and restarting rotates the view token.
If the GUI-owned service was active at shutdown, the next launch offers its
previous address/port with a default-No consent dialog. Only explicit Yes starts
it; Stop, No and failed restart leave it off. The saved preference stores no web
token. The browser receives this separate token, never the broker credential. The link
grants private session/message viewing, operator messaging and Prism interaction;
share it only with intended operators.

The server checks bearer authentication, exact Host/Origin, fixed routes and
bounded strict JSON. It exposes sanitized session/message data and a fixed send
operation, rather than general broker RPC or terminal/admin controls. Published
Prism content cannot execute scripts or load remote resources. Typed gadget
events record intent, not command execution or approval. Exact request IDs
protect uncertain message and gadget retries.

The built-in listener uses HTTP. Remote devices need an operator-managed trusted
network/VPN, or a correctly configured HTTPS gateway that validates the public
origin before forwarding. VISOR does not configure public hosting, firewall rules
or TLS. Browser drafts and pending retries remain in that browser's local
storage; native drafts and SQLite state retain their existing local protections.
