# Beta status and evidence

Release candidate: **0.1.0b1**, build `2026-10-20-beta-1`.

Beta is an evaluation label. It does not mean complete platform acceptance, completed binary-distribution review, or production suitability.

| Area | Evidence available | Still unverified |
| --- | --- | --- |
| Windows | Earlier native builds and extracted-package checks; disposable Codex delivery/completion and deletion checks; operator-confirmed Focus | This beta's native rebuild, clean machine, reboot recovery, monitor/DPI/accessibility and all provider combinations |
| Linux/X11 | Ubuntu/KWin source/package checks and disposable terminal controls; regression checks for title changes, close verification and desktop PATH | Full provider workflows, real provider-history deletion, reboot, multiple monitors and clean-machine acceptance |
| macOS | Quoted launch commands, identity guards, multi-tab protection, minimized-window restore, Automation metadata and packaging workflow implemented | **No live Mac build or desktop testing.** AppleScript compiler checks, signing, permissions, Finder and Terminal behavior await a Mac/hosted runner |
| Wayland | Explicit capability errors and documented XWayland launch route | Native window controls unavailable |
| Claude Code | Simulated discovery/hooks/MCP/channel and restart checks | Live Claude model behavior; external channel authentication, policy and approval |
| Codex | Earlier Windows live checks; Linux selected installed 0.163.0; exact resume and daemon startup regression checks | Full platform/version matrix and reliable peer-tool attachment on already-loaded threads |

The beta Linux suite passed 150 tests with five skips: four Windows-only checks and the native AppleScript compiler check. This includes the delayed-response broker shutdown regression and explicit Mac launch-environment forwarding checks. Ruff and frozen offline lock validation passed. Linux beta packaging and extracted-package checks passed during preparation; Mac and Windows beta binaries still need their own native runs. Earlier Windows/Linux reports describe the builds and scopes actually tested; they are not results for a newly labelled binary.

See [Windows evidence](WINDOWS_ACCEPTANCE.md), [Linux evidence](LINUX_ACCEPTANCE.md), [Mac preparation](MACOS.md), [open work](ROADMAP.md), and the [native test checklist](NATIVE_ACCEPTANCE.md). Hosted CI, publication and release downloads remain pending.
