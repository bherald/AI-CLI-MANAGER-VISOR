# VISOR 1.5.0 beta 6 communication candidate

Build: `2026-11-01-details-r7`. Proposed tag: `v1.5.0-beta.6`.
This is a local preparation, not a published release.

## Changes

- Deliver messages to independent Codex recipients concurrently through the
  existing durable queue. A slow recipient or its deletion no longer holds the
  shared admission lock. Preserve per-recipient ordering, policy and UNKNOWN
  outcomes. [Configuration and recovery](COMMUNICATION-DELIVERY.md).
- Persist native Send's exact request before dispatch and retain it across a
  lost response or restart. Retry reconciles the same request instead of creating
  another message. Full Unicode stays within the existing typed text limits.
- Show the exact recipient-bound final reply in the native receipt inspector;
  allow a deliberate follow-up without replacing an existing pending draft.
- Make held, failed and uncertain messages visible under Needs attention.
  [Native message guide](NATIVE_MESSAGES.md).
- Retain safe HTTP(S) links and bounded table formatting in Prism text cards.
  Native links now open through explicit mouse or keyboard activation; native and
  browser rendering preserve the same validated colors, borders and spacing.
  [HTML formatting and safety](PRISM_HTML.md).

## Evidence and limits

Focused source tests reproduce the original failures before correction and pass
afterward. Independent tests include a slow target being deleted while healthy
delivery and policy changes proceed, with no native model/provider calls. Final
combined source, smoke, extracted-package and privacy results are retained with
the exact candidate commit by the release preparation workflow.

The last published release remains
[beta 5](https://github.com/bherald/AI-CLI-MANAGER-VISOR/releases/tag/v1.5.0-beta.5).
Its hosted checks do not verify this candidate. Windows and macOS native builds,
interactive platform acceptance and binary redistribution review remain separate
from local Linux checks and the MIT first-party source candidate. Preserve the
existing dependency notices and [distribution review](DEPENDENCY_REVIEW.md).

## Upgrade, rollback and publication

Use a matched supervisor and broker after private-state backup and normal
in-flight request reconciliation. Preserve unresolved draft and message custody;
do not force a resend or kill shared provider processes. Retain the previous
matched package. Rollback must first reconcile or preserve the new pending draft,
since an older composer cannot interpret that field safely.

The public source candidate excludes private history, runtime profiles, tokens,
operator records and private planning files. New screenshots use synthetic data.
Freeze the exact commit, export, extract and inspect the manifest before proposing
the exact repository, branch, commit set and release content for approval. No
push, tag, public release or hosted-CI result is implied by local preparation.
