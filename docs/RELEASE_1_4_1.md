# VISOR 1.4.1 beta — release evidence

Release: [v1.4.1-beta.2](https://github.com/bherald/AI-CLI-MANAGER-VISOR/releases/tag/v1.4.1-beta.2).
This is an evaluation beta. Read the versioned release for the exact source
commit, hosted job results, available platform assets and SHA-256 checksums.

## Completed work

- Compact Prism title/update header in the system timezone, a small Actions
  accordion, and a single-line 8pt footer. Owner/revision/provenance and full
  errors remain in safe tooltips. The README screenshot uses synthetic content.
- GitHub #4: provider-title refresh, deliberate aliases, dual-name search,
  provider-title tooltips and explicit re-registration names. Migration preserves
  legacy custom names as possible aliases because their origin was not recorded.
- GitHub #5: persist a fresh Codex thread's initial name using the supported API,
  close the creator, verify exact-ID resume from another client, then launch its
  terminal. Refuse an unconfirmed handoff with an actionable error.
- GitHub #6: expire stale Claude live statuses after successful discovery,
  distinguish unavailable discovery, retain history and fresh managed hook state.
- The source baseline also includes recipient-bound operator final replies via
  MCP/RPC. Reply posting is separate from acknowledgment, completion and approval.

Implementation is marked done in [ENHANCEMENTS.md](ENHANCEMENTS.md). Defect
resolution links and verification limits are attached to the corresponding issues.

## Validation before publication

- Full local source suite: 469 tests, 463 passed and six platform checks skipped.
- Ruff, frozen offline dependency lock check and whitespace checks passed.
- Credential-pattern source/history scan found no matches; this is a bounded
  pattern scan, not complete disclosure or redistribution clearance.
- Local Linux native package/extraction checks passed: checksums, binary/notices,
  relocated UI, singleton, broker restart/queue, simulated Claude, upgrade and
  private backup/recovery. An actual prior-1.4.0 helper and worker survived the
  isolated upgrade test. The verified local package opened through the shortcut.
- The Codex 0.164.0 handoff failure was reproduced in an isolated temporary
  app-server without model turns. Initial naming made default, legacy and
  paginated threads resumable by a second client after creator closure.

The first hosted Windows attempt exposed an unclosed connection in the new
migration test cleanup. The fixture now uses the store's explicitly closing
transaction context. The final beta tag reruns all platforms with that correction.

## Limits retained

Hosted tag builds produce the public packages from sanitized public source;
local binaries are not renamed or substituted for another platform. Consult the
release for results, rather than treating a workflow definition as evidence.
Interactive Windows Codex TUI, macOS desktop/Automation, clean-machine,
monitor/DPI/accessibility and live Claude checks remain unclaimed where not
explicitly recorded. Wayland window controls remain unavailable through X11.
Windows/Linux packages are unsigned; Mac packages are experimental, ad-hoc signed
and not notarized. Dependency/source-replacement redistribution review remains
open. No stable/production-suitability claim is made.
