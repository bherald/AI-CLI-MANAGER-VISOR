# Beta status and evidence

Release candidate: **1.2.0**, build `2026-10-20-beta-3`.

Beta is an evaluation label. It does not mean complete platform acceptance, completed binary-distribution review, or production suitability.

| Area | Evidence available | Still unverified |
| --- | --- | --- |
| Windows | Earlier native builds and extracted-package checks; disposable Codex delivery/completion and deletion checks; operator-confirmed Focus | This beta's native rebuild, clean machine, reboot recovery, monitor/DPI/accessibility and all provider combinations |
| Linux/X11 | Ubuntu/KWin source/package checks and disposable terminal controls; regression checks for title changes, close verification and desktop PATH | Full provider workflows, real provider-history deletion, reboot, multiple monitors and clean-machine acceptance |
| macOS | Quoted launch commands, identity guards, multi-tab protection, minimized-window restore, Automation metadata and packaging workflow implemented | Initial hosted jobs compiled AppleScript but failed helper signing / broker startup deadlines. Follow-up uses a console-enabled nested app and bounded diagnostic readiness waits; a successful package and desktop acceptance remain unverified |
| Wayland | Explicit capability errors and documented XWayland launch route | Native window controls unavailable |
| Claude Code | Simulated discovery/hooks/MCP/channel and restart checks | Live Claude model behavior; external channel authentication, policy and approval |
| Codex | Earlier Windows live checks; Linux selected installed 0.163.0; exact resume and daemon startup regression checks | Full platform/version matrix and reliable peer-tool attachment on already-loaded threads |

The Mac-CI follow-up Linux suite passed 156 tests with five skips: four Windows-only checks and the native AppleScript compiler check. This includes the delayed-response broker shutdown regression and explicit Mac launch-environment forwarding checks. Ruff and frozen offline lock validation passed. Linux beta packaging and extracted-package checks passed during preparation; Mac and Windows beta binaries still need their own native runs. Earlier Windows/Linux reports describe the builds and scopes actually tested; they are not results for a newly labelled binary.

See [Windows evidence](WINDOWS_ACCEPTANCE.md), [Linux evidence](LINUX_ACCEPTANCE.md), [Mac preparation](MACOS.md), [open work](ROADMAP.md), and the [native test checklist](NATIVE_ACCEPTANCE.md). The public source and articles are published. Initial hosted Windows/Linux CI passed; Mac follow-up CI and release downloads remain pending.
