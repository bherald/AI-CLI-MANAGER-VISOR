# VISOR 1.4.1 completed features

| Feature | Implementation status | Validation / remaining acceptance |
| --- | --- | --- |
| Compact Prism title and Updated time | **Done** | System timezone/DST tests; broker revision time; older/local file-time fallback |
| Compact Actions accordion | **Done** | Collapsed default, saved state and narrow-width toolbar overflow checked |
| Single-line 8pt Prism footer | **Done** | Source rendering and layout tests; full text in safe tooltip |
| Provider titles and intentional aliases | **Done** | Refresh, migration, search, tooltip and re-registration regressions; GitHub #4 |
| Fresh Codex session handoff | **Done** | Initial title persistence and second-client resume; isolated Codex 0.164.0 reproduction/regressions; GitHub #5; native Windows TUI check remains |
| Claude stale-status reconciliation | **Done** | Empty/saved/failed discovery and fresh managed-hook cases; GitHub #6 |
| Recipient-bound operator final reply API | **Done** | Scoped MCP/store/RPC regressions; read/completion/approval remain separate |
| README, screenshot and release notes | **Done** | Synthetic source preview; public beta platform limits retained |

Done means implemented and covered by the stated checks. It does not mean every
native desktop/provider combination passed interactive acceptance. See
[release evidence](RELEASE_1_4_1.md) and the [remaining roadmap](ROADMAP.md).

## VISOR 1.3.1 enhancement checklist

This checklist records the 1.3.1 beta candidate's local validation on 2026-10-22. Hosted Windows/Mac CI and interactive platform acceptance are not established by local Linux tests.

| Review item | Candidate implementation | Remaining acceptance |
| --- | --- | --- |
| Message selection changes after refresh | Preserve selected message IDs across insertion, removal and polling; deleted selections clear | Regression included |
| Old supervisor silently activated by a new download | Visible UI version; About with build, executable, broker and provider diagnostics; version-aware activation with legacy fallback | Native Windows/Mac IPC and desktop checks |
| Broker failure needs manual recovery | Automatic reconnect with capped backoff; explicit Stop stays stopped; stale snapshots cannot overwrite a new connection | Crash/reboot and Windows service interactions |
| Interactive Windows acceptance | Expanded repeatable checklist in NATIVE_ACCEPTANCE.md | Windows foreground, minimized windows, multiple monitors and DPI still require a desktop |
| Unfinished operator message lost on restart | Debounced durable draft with recipient; serialized writes; close flush and save-failure warning; missing recipient requires explicit choice | Native desktop restart acceptance |
| Backup/restore | Private consistent SQLite snapshots, bounded retention, opt-in pre-upgrade backup; offline recovery with queue holds and credential/link reset | Windows ACL and unusual-filesystem acceptance |
| Terminal appearance | xterm font/point size; named Windows Terminal and macOS Terminal profiles for new consoles | Native Windows/Mac launch/profile checks |
| Documentation | Done: current downloads, appearance, recovery and actual validation limits described | Included in the approved beta release |
| Build cache and disk space | Space preflight; explicit cleanup preview/apply preserving selected/running/recent bundles | Continue measuring native packaging headroom |

Terminal appearance changes apply to new console windows. Windows/Mac profile fonts are configured in the terminal application's own profile settings. Existing sessions and console settings are left in place.

Keep native Wayland controls, live Claude model/channel acceptance, detached-work detection limits and dependency-distribution review on the broader [roadmap](ROADMAP.md).

## Usability follow-ups

Use Microsoft's Fluent 2 accessibility guidance for both desktop and future web
interfaces, with native platform conventions for keyboard focus and controls.
These are design references, not a claim of conformance or a web-version plan:
https://fluent2.microsoft.design/accessibility
https://learn.microsoft.com/en-us/windows/apps/design/input/keyboard-interactions

Future candidates: pinned sessions/project groups; a session details pane;
archive/hide with undo; adjustable supervisor text size/density; keyboard action
palette/help; Open project folder/Copy path; unread-message indicators; clearer
terminal-versus-provider connection states. These remain proposals, separate
from the current compact rows, saved descriptions and confirmed cleanup controls.
