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

The maintainer is [bherald](https://github.com/bherald). For a published repository, use its Security tab and **Report a vulnerability** when private vulnerability reporting is enabled. During release preparation that route is not configured yet. Do not disclose a vulnerability containing credentials or private transcripts through a public issue or profile comment. Enabling and verifying private reporting is a repository-publication step.
