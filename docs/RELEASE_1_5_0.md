# VISOR 1.5.0 local review candidate

Build: `2026-10-27-prism-table-agent-ui-r5`.

The operator approved local implementation, testing, Linux packaging and desktop
shortcut delivery. Public source publication, tags and release assets are held
for a separate approval after review.

## Changes

- One resizable Prism workspace with board navigation, hide/show controls,
  readable session names, compact headers and closed secondary actions.
- Portable rich gadgets and typed direct input, with controls outside graphics.
- Optional agent templates and catalog, freeform JSON boards, safe rich text,
  emoji and contained raster assets.
- Responsive authenticated browser UI for desktop, tablet and phone layouts.
- Standard multi-row selection, stable selection through refresh/sort, and
  guarded bulk actions with partial-error reporting.
- Local layout undo/redo, snap and layer controls; preserved board/draft lifecycle.

See [Prism design](PRISM_DESIGN.md), [authoring](../PRISM.md) and
[feature checklist](ENHANCEMENTS.md).

Web access is completely optional and off by default. Explicit Start/Stop controls
choose a local or specific LAN/VPN address. The browser combines session status,
recent messages, an operator composer with exact receipts/final replies, and Prism
boards. The private view token never grants arbitrary broker RPC.

## Additive table and agent work

This packet keeps version **1.5.0**, with the distinct build ID above.

- Styled tables add bounded border color/width/style, header/column/row/cell
  formatting, badges, density, wrapping and optional sort/filter/copy.
- Stable row IDs retain local selection, column sizes, scroll, filters and sort
  across ordinary updates. Small native cards scroll instead of overlapping.
- Plain help and loading/empty/error/stale cues include optional source/time.
- If web access was active at shutdown, a once-per-launch dialog offers the old address/port with No selected; only Yes starts a new listener/token. Stop, No and failure clear the reminder.
- MCP discovery includes focused machine-readable schemas, presets, policy bounds
  and current-helper CLI guidance for clients with cached tool lists.
- Read-only authoring preflight checks schema/revision/ownership/assets. Actual
  MCP stdio and CLI tests cover discovery through publication, update and input.
- [Web operation](WEB_ACCESS.md) and [agent workflow](PRISM_AGENTS.md) guides cover
  startup/lifetime, mobile use, privacy, retries, troubleshooting and ownership.
- Release screenshots are synthetic captures of this implementation, prepared for
  replacement on public surfaces only after platform-specific approval.

VISOR provides reusable UI/web/MCP capabilities; consuming applications own their
workflows and best practices. Existing consumers retain their domain-specific workflow and hosting ownership.
The reusable browser surface is delivered and operated with VISOR.

## Verification

Source verification on Linux/X11 (2026-10-27):

- Full isolated suite: **590 tests run, 584 passed, six platform checks skipped**.
- Ruff across source/tests/scripts, frozen offline lock check and diff whitespace checks passed.
- Source UI smoke rendering passed. Synthetic native Prism and real Chrome desktop/390px mobile views were inspected.
- Tests cover typed event/receipt ownership, exact uncertain retries across restart, current sharing/policy, quiet dismissal, drafts, themes, modifier selection, explicit web Start/Stop and restart consent.
- Optional web is off by default. Synthetic tests confirm fixed authenticated routes and session/message DTOs without broker credentials or process identities. Direct and IPv4-mapped wildcard/multicast binds are rejected.
- Additional native lifecycle checks cover duplicate board titles without displaying IDs.
- Public source/history pattern audit is clean; this is a pattern scan, not a complete disclosure/license review.

The Linux native package passed relocated UI/broker/queue, single-instance,
prior-helper upgrade, state recovery and frozen authenticated VISOR/Prism browser
checks. The final delivery archive includes its SHA-256 receipt.

Local desktop delivery completed on 2026-10-27. The desktop launcher opens the
1.5.0 supervisor; its broker reports the matching build. Existing session/message
identities and message states were preserved, along with the running older
terminal helper/worker. The web feature remained off without a GUI TCP listener.
Automated fixtures use isolated state and synthetic content; no model requests
are required. Real-device/native-platform acceptance remains separate below.

## Acceptance boundaries

Linux/X11 is the local review platform. Native Windows/Mac, real iOS/Android
devices, complete assistive-technology testing and dependency-distribution review
remain open. The browser UI is responsive; this release does not include separate
native iOS/Android applications. Remote browser access requires an explicitly
chosen interface and an operator-managed protected network/HTTPS route.

## Release images and publication

Eight current-renderer screenshots use only synthetic data: native supervisor/empty view, Prism overview/styled tables, and VISOR/Prism desktop/mobile browser views. Native captures are reproducible with `scripts/make_release_images.py --native-only`. Images, README, web/agent guides and checksums are prepared locally; public replacements await operator approval. Existing website/social assets are audited and updated under their own exact platform approval.
