# 1.5.0 beta 5 — published 2026-10-27

[Download and checksums](https://github.com/bherald/AI-CLI-MANAGER-VISOR/releases/tag/v1.5.0-beta.5). Exact-tag source, native-build and extracted-package jobs passed on Windows, Ubuntu, Apple Silicon Mac and Intel Mac. Interactive/native-device limits remain open. Beta 5 fixes test-fixture privacy and IPv6-format assumptions without changing the reviewed runtime build. The feature list below describes the 1.5.0 implementation.

# 1.5.0 local candidate — rich Prism workspace

- Consolidate floating Prism boards into one resizable desktop workspace with wide previous/next controls, a readable board picker and compact connected-session names.
- Put secondary board/card controls and messages in default-closed accordions. Preserve drafts, saved layouts, quiet dismissal/reopen and the concise title/time and 8pt footer.
- Add gauges, meters, progress, indicators, counters, statistics, sparklines, timers, tables, arrows, buttons, toggles, sliders and selectors. Keep direct controls below graphics, with typed contributor-addressed events and exact retry receipts.
- Retain safe rich HTML, emoji, contained PNG/JPG images and freeform layouts. Add layout undo/redo, snapping and layer controls; expose a reusable agent catalog and optional templates.
- Add the optional authenticated responsive VISOR/Prism browser view for desktop/tablet/phone. Loopback is the default; remote bind addresses require an explicit operator choice. The browser never receives the broker credential.
- Standardize session/connection/board terms and multi-row selection: click, Ctrl/Cmd toggle, Shift range, Ctrl/Cmd+A, stable selection across refresh and guarded bulk actions with partial-error reporting.
- Add portable styled tables (borders/colors/typography/badges) with optional sort/filter/copy and retained row/column state.
- Add plain help and honest loading/empty/error/stale data cues, with small-card scrolling.
- Expose machine-readable MCP schemas, presets, effective limits, current-helper CLI guidance and read-only publication preflight.
- Offer the previous web listener at the next normal launch only if it was active at shutdown, using a default-No consent dialog and a fresh token; Stop/No clears the reminder.
- Extend agent and web-service setup/operation documentation; prepare matching synthetic release pictures.
- Update authoring, design references, examples and release documentation.

Public source, the beta 5 release and all four platform packages are available. See [release evidence](RELEASE_1_5_0.md). Interactive Windows/Mac, physical mobile devices and complete accessibility acceptance remain open.

# 1.4.1 beta — compact Prism and discovery fixes

- Show only the board title and local update date/time in Prism's header.
  Record broker-assigned revision timestamps and preserve them across retries
  and projection repair; older/local documents use source modification time.
- Move board actions into a compact, initially collapsed accordion. Keep all
  actions accessible through toolbar overflow at narrow widths and remember the
  local expanded state. Use a concise, single-line 8pt footer with full hover text.
- Include the recipient-bound `operator_reply` MCP operation and operator-only
  `message_get` lookup already implemented in the source baseline. A final reply
  remains separate from read acknowledgment, task completion and action approval.
- Fix [#4](https://github.com/bherald/AI-CLI-MANAGER-VISOR/issues/4): refresh provider
  titles independently of explicit VISOR aliases, search both, show differing
  provider titles in tooltips, and apply requested names on re-registration.
  Preserve old names as possible aliases because their original provenance is unknown.
- Fix the reproduced handoff in [#5](https://github.com/bherald/AI-CLI-MANAGER-VISOR/issues/5):
  save a fresh Codex thread's initial title through `thread/name/set`, then verify
  its exact ID can resume through a second client after the creator closes.
  No model turn, synthetic prompt, arbitrary delay or provider database edit is used.
  Refuse terminal launch with an actionable error if persistence/handoff fails.
- Fix [#6](https://github.com/bherald/AI-CLI-MANAGER-VISOR/issues/6): expire stale
  Claude live statuses after successful empty discovery, distinguish unavailable
  discovery as unknown, retain saved history and fresh managed hook/heartbeat evidence.

Local Linux build and desktop shortcut delivery completed on 2026-10-27.
The locked source pipeline, extracted-package acceptance, actual prior-1.4.0
helper upgrade and desktop launch checks passed. Native Windows/TUI acceptance
remains pending.
See the [release](https://github.com/bherald/AI-CLI-MANAGER-VISOR/releases/tag/v1.4.1-beta.2)
for exact hosted package results and checksums, and the [completed feature and
defect checklist](RELEASE_1_4_1.md) for source validation and remaining limits.
Interactive Windows/Codex TUI acceptance remains separate from hosted builds.

# 1.4.0 beta — Prism shared workspaces

- Add shared Prism windows with movable cards for formatted notes, pictures,
  diagrams, charts, questions and freehand drawings. Operators can type and draw;
  Codex and Claude sessions with the required connections can contribute their own cards.
- Give already-running sessions scoped Prism access without restarting them.
  Revision checks protect newer content; saved replies remain recipient-scoped.
- Preserve an operator's window dismissal across updates and restarts. Session
  reopen requests produce a quiet indicator; the operator can reopen through
  the chooser or the authenticated operator control path. Presentation is not read.
- Add **Manage Prism windows…** to the main menu. Delete one explicitly selected
  saved board after confirming its exact revision. Remove its history, replies,
  membership and local drafts; retain shared images, backups and provider sessions.
  Retry uncertain results with the same saved identity, and reject stale recreation.
- Correct operator receipt polling: previously the GUI was denied and silently
  kept waiting after the recipient had read a saved reply. Poll only board-scoped
  metadata, display read separately from completion, and show transport failures.
- Use muted backgrounds, readable headers and a slightly lighter drawing surface.
- Report durable revision acceptance separately from a pending display projection.
  Exact retries recover the saved result without creating duplicate revisions.
- Keep unchanged board polling lightweight, and stage projection work outside the
  database writer lock. Revision and deletion checks reject stale publication.
- Apply changed policy to open windows without truncating drafts or drawings.
  Invalid policy holds content operations; navigation and draft saving remain available.
- Scroll question cards at small restored sizes so prompts, answers, controls and
  full receipt labels remain reachable.
- Destroy retired Prism windows on their GUI thread, keeping delayed replies
  from accessing deleted widgets or recreating removed drafts.
- Reject drive-less rooted image paths on Windows before filesystem access,
  and normalize temporary-path expectations in Mac and Windows tests.

Verification for this unreleased candidate is recorded with its local delivery.
Native interactive Windows/macOS Prism acceptance and live Claude model behavior
remain unverified. The existing platform/package limits below still apply.

# 1.3.1 beta 1 — communication and desktop usability

- Make session communication a documented core workflow: explicit pairs,
  participation, operator messaging, delivery/read/completion states and local history.
- Separate the supervisor into Sessions, Connected sessions and Messages; fit
  session rows to content and remove the unused middle gap.
- Prefer Konsole on Linux with the normal desktop profile/font/colors; keep
  xterm fallback and an explicit terminal selector. Preserve quoted cwd/arguments,
  stable titles, focus/close identity checks and separate terminal windows.
- Isolate native Konsole from the frozen application's Qt library/plugin paths.
- Tighten both grids to 21-pixel rows and retain sortable columns/identity selection.
- Show saved-session descriptions from existing provider metadata without model
  calls; improve generic UUID labels while preserving custom names.
- Support Delete on selected session/message rows, with Yes/No confirmation
  defaulting to No and existing provider/terminal deletion safeguards.
- Filter message history by All/Needs attention/Finished; confirm cleanup of loaded
  completed/failed records and bulk removal of selected connections. Uncertain
  delivery is not silently retried; message cleanup is distinct from transcript deletion.
- Explain coordination history, provider transcripts, diagnostics and startup
  logging, including their privacy and verification limits.

Validation: the versioned source suite ran 216 tests (210 passed, six skipped).
The focused layout suite ran 32 UI tests (31 passed, one skipped). Lint and frozen
lock validation passed. Real disposable Linux/X11 Konsole and xterm checks, the packaged
synthetic-provider workflow, relocated startup, broker restart, singleton, upgrade
and state recovery passed. The exact 1.3.1 tag CI provides this release's native
Windows, Linux and Mac package evidence; published release notes link its results.
Interactive macOS desktop acceptance remains unverified; Mac packages remain
experimental, ad-hoc signed and not notarized. Windows/Linux packages are unsigned.
No stable/production-suitability or complete accessibility/redistribution review
is implied by this beta.

# 1.3.0 — beta candidate

Local pre-release validation is recorded here; exact native-build outcomes belong in the versioned release's published notes.

- Preserve communication-row selection by message ID so arrival/deletion cannot redirect a selection.
- Show the running version, build/executable diagnostics, and notices when a different/older supervisor is activated by a new download. Preserve legacy activation support.
- Reconnect after broker failure with bounded backoff; preserve an intentional stop and ignore obsolete snapshots.
- Persist unfinished operator drafts and recipients, serialize saves, and flush on close with save-failure warnings. Missing recipients require explicit selection.
- Add new-console appearance settings: Ubuntu/xterm font and point size, Windows Terminal profile name/GUID, macOS Terminal profile name.
- Add private SQLite backup snapshots, retention, opt-in pre-upgrade backup and conservative offline recovery. Restore holds queued messages and invalidates old credentials/terminal links; it does not restore provider history.
- Add native desktop acceptance steps, current download documentation, build-space preflight and explicit inactive-build cleanup.

Local/mocked tests do not establish interactive Windows/Mac acceptance. Consult the versioned release notes for hosted-build and download evidence.

# 1.2.4 — double-click console focus

- Double-click a session row to focus and raise its verified existing console, including linked external consoles. Sorting preserves the clicked session identity; double-clicking never starts a second console.

# 1.2.3 — session list and Linux desktop improvements

- Add Last used dates from provider activity or VISOR launch, without treating refresh/heartbeat as use.
- Sort every session column; preserve sorting and selected session identities across refreshes and save sorting with UI preferences.
- Add classic overlapping Cascade windows with visible title bars and a clearly labelled Stacked tile action, with pixel-complete tiled layouts.
- Show horizontal, vertical and diagonal resize cursors and restore normal widget cursors away from the border.
- Reduce session and communication rows to 25 pixels and tighten control padding and spacing.
- Clarify the VISOR terminal filter, explain empty filtered lists, and count working/waiting sessions among the displayed rows.

# 1.2.1 — Windows npm Codex compatibility

Fixes the “native provider executable is required” error when Windows finds npm's `codex.cmd`, `codex.ps1` or extensionless shim. VISOR locates the selected installation's declared `@openai/codex` entry point and invokes it with `node.exe`. This preserves upstream vendor/helper paths, package metadata, standard input/output and literal command arguments. Resume, discovery, daemon startup, delivery and `--doctor` use the same command prefix. Native executables remain supported.

The Windows workflow installs a disposable, pinned Codex 0.163.0 npm fixture and checks version resolution in both source and the frozen app. It does not authenticate or run a model. Native Windows tests also pass spaces, quotes, Unicode and shell metacharacters through a real Node process.

Version 1.2.0's published evaluation downloads remain at [the beta release](https://github.com/bherald/AI-CLI-MANAGER-VISOR/releases/tag/v1.2.0). CI artifacts identify the new source revision and contain the 1.2.1 packages.

# 1.2.0 — beta

Message history now supports deleting selected messages in every delivery state, including uncertain delivery. Deletion cancels queued delivery, removes message text from Visor, and preserves request IDs against accidental replay. Already delivered text cannot be retracted. Cleared history records are hidden from the communication list.

Version 1.2.0 is a beta evaluation release. The version-tag workflow builds Windows, Linux, Apple silicon and Intel archives and adjacent SHA-256 checksums. Release notes record which exact tag jobs passed.

VISOR supervises independent Codex and Claude Code terminals: exact-session resume, verified Focus/arrangement, operator messages, approved session pairs and tracked wrap-up. Existing terminal windows remain usable independently.

The broker now waits for in-flight HTTP responses before exiting, preventing a truncated stop acknowledgment.

This candidate adds desktop shell PATH resolution for version-manager provider installations, shared Codex daemon startup verification, Linux title/closure fixes, and further macOS preparation. Mac launches explicitly carry the resolved PATH and source-mode PYTHONPATH into Terminal. Other Mac changes include restoring minimized Terminal windows, clearer refusal reasons, avoiding unintended Terminal relaunch during missing-window checks, and validating the app's Automation usage description before signing and after extraction.

macOS remains experimental. Hosted Apple silicon and Intel jobs now pass source tests, native AppleScript compilation, offscreen startup, app/helper signature checks, extracted-package acceptance and upgrade checks. Fixes include a proper nested console helper app, loopback startup without reverse DNS, and relocating the complete helper bundle for the upgrade fixture. Interactive desktop and live Claude model validation remain unclaimed.

Windows/Linux packages are unsigned. Mac packages use local ad-hoc signing, without Developer ID notarization. Clean-machine acceptance and the dependency-distribution review remain open; the included notices and native inventories do not certify completion of that review.

See [STATUS.md](STATUS.md), [ROADMAP.md](ROADMAP.md), [MACOS.md](MACOS.md), and [SECURITY.md](../SECURITY.md) before relying on this evaluation build.
