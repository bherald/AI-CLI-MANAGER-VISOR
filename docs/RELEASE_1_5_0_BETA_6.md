# VISOR 1.5.0 beta 6

Build: `2026-11-02-quiet-publisher-r12`. Release tag: `v1.5.0-beta.6`.
Published November 2, 2026, from source
[`c633c406`](https://github.com/bherald/AI-CLI-MANAGER-VISOR/commit/c633c406fdadd2f50901bdac9850a1e1a79a9bbf).
[Download beta 6 and its checksums](https://github.com/bherald/AI-CLI-MANAGER-VISOR/releases/tag/v1.5.0-beta.6):

| Platform | Archive |
| --- | --- |
| Windows x64 | `visor-1.5.0-windows-amd64.zip` |
| Linux x86_64 | `visor-1.5.0-linux-x86_64.tar.gz` |
| Apple silicon Mac | `visor-1.5.0-darwin-arm64.tar.gz` |
| Intel Mac | `visor-1.5.0-darwin-x86_64.tar.gz` |
| Matching source | `visor-source-1.5.0-public.tar.gz` |

Each archive has an adjacent `.sha256` file. Later documentation updates on
`main` do not change this tag, source archive or the reviewed binaries.

## Changes

- Routine agent messages stay in auditable quiet inboxes. Agents explicitly pull
  and acknowledge them; operator messages and explicit attention requests keep
  their normal visible delivery. A quiet message does not secretly wake an idle
  Codex worker. See [communication delivery](COMMUNICATION-DELIVERY.md).
- Delegate publication of exact Prism cards to an existing permitted session
  without changing their contributors or operator-command recipients. Grants
  are revision checked and revocable. No card removal/recreation is needed.
  See [scoped publishing](DELEGATED_PUBLISHING.md).
- Resume selected saved sessions with fresh per-provider checks and honest
  per-session launch/skipped/error results. Existing workers and uncertain
  ownership remain protected. See [session recovery](SESSION_RECOVERY.md).
- Save generated or operator-selected static browser tokens, retain the selected
  mode across reopening, and revoke old access when rotating a saved token.
  Correct Windows path/descriptor file identity checks without relaxing private
  permissions, replacement checks or mutation detection. See [web access](WEB_ACCESS.md).
- Preserve durable native Send, independent-recipient delivery, exact replies,
  typed text limits and UNKNOWN outcomes. See [native messages](NATIVE_MESSAGES.md).
- Retain validated Prism links, readable tables, borders and details. Provider
  approval boundaries and explicit direct-session connections still apply.

## Verification and release status

The exact release source passed [all four native CI jobs](https://github.com/bherald/AI-CLI-MANAGER-VISOR/actions/runs/41507797095),
including extracted-package checks on Windows, Linux and both Mac architectures.
Focused quiet-inbox and scoped-publishing checks passed together. A local Linux
package upgrade preserved saved boards, credentials, approved pairs and the same
live terminal helpers; an authenticated loopback listener was restored normally.
No provider/model process was signalled. These results do not claim interactive
Windows/Mac, physical-device or accessibility acceptance.

Builds omit an unused optional Readline module. They retain the complete pinned
Qt and ICU notices, exact source-access/replacement directions and available
native copyright metadata. The published archives' actual inventories, notices
and matching source were reviewed separately for each platform. See [dependency review](DEPENDENCY_REVIEW.md)
and [source access](DEPENDENCY_SOURCES.md).

The Windows archive includes OpenSSL 3.5.9. Its complete Apache 2.0 license is
byte-identical to the bundled OpenSSL 3.5.5 notice; the notice's directory name
does not identify the DLL version. [Corresponding OpenSSL 3.5.9 source](https://github.com/openssl/openssl/releases/download/openssl-3.5.9/openssl-3.5.9.tar.gz).

Successful main pushes, manual builds and version-tag builds retain evaluation
archives/checksums for seven days. The published beta 6 release retains its
reviewed downloads separately. [Beta 5](https://github.com/bherald/AI-CLI-MANAGER-VISOR/releases/tag/v1.5.0-beta.5)
remains available as an earlier release.

## Upgrade and rollback

Back up private state, reconcile in-flight requests and close the supervisor with
**Leave running and close**. Upgrade the matched supervisor/broker while retaining
older helpers and a rollback package. Saved profiles, tokens and provider history
are not part of the public source or release archives.

Extract the complete new package and update any desktop shortcut to its
`visor/visor.exe`, `visor/visor` or `visor.app`. Open **About / diagnostics** and
confirm the running build is `2026-11-02-quiet-publisher-r12` and the executable
belongs to the new installation. A shortcut change does not replace a running process.

Before downgrading, revoke a scoped publisher and stop its caller. Hold pending
agent traffic and preserve quiet, held and UNKNOWN messages; an older broker must
not reinterpret them as visible prompts. Do not force a resend, restore old state
as a normal upgrade shortcut or terminate unrelated terminals.
