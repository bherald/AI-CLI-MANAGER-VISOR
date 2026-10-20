# Beta status and evidence

Release candidate: **1.2.0**, build `2026-10-20-beta-3`.

Beta is an evaluation label. It does not mean complete platform acceptance, completed binary-distribution review, or production suitability.

| Area | Evidence available | Still unverified |
| --- | --- | --- |
| Windows | Earlier native builds and extracted-package checks; disposable Codex delivery/completion and deletion checks; operator-confirmed Focus | Clean machine, reboot recovery, monitor/DPI/accessibility and all provider combinations |
| Linux/X11 | Ubuntu/KWin source/package checks and disposable terminal controls; regression checks for title changes, close verification and desktop PATH | Full provider workflows, real provider-history deletion, reboot, multiple monitors and clean-machine acceptance |
| macOS | Quoted launch/identity guards and Automation metadata; hosted source tests, AppleScript compilation, Apple silicon/Intel builds, signatures, extracted-package and upgrade checks pass | Interactive desktop, Automation permissions, Gatekeeper and clean-machine acceptance |
| Wayland | Explicit capability errors and documented XWayland launch route | Native window controls unavailable |
| Claude Code | Simulated discovery/hooks/MCP/channel and restart checks | Live Claude model behavior; external channel authentication, policy and approval |
| Codex | Earlier Windows live checks; Linux selected installed 0.163.0; exact resume and daemon startup regression checks | Full platform/version matrix and reliable peer-tool attachment on already-loaded threads |

The local 1.2 suite contains 167 tests, including deletion in every message state, multi-selection, late receipts, replay protection, operator-only deletion, and responsiveness during slow provider delivery. Linux skips the five Windows/native-AppleScript checks. Ruff, frozen offline lock validation and public-source/history credential scans passed. The Linux frozen package passed deletion/restart/access checks as well as relocated startup and upgrade acceptance.

The [hosted follow-up](https://github.com/bherald/AI-CLI-MANAGER-VISOR/actions/runs/39462649997) passed both Mac architectures, including native packaging and extracted acceptance. The final release tag reruns all platform jobs and additionally runs the deletion regression against each packaged broker. Published release notes identify the exact tag-build outcome. Interactive Mac acceptance and the dependency-distribution review remain open.

See [Windows evidence](WINDOWS_ACCEPTANCE.md), [Linux evidence](LINUX_ACCEPTANCE.md), [Mac preparation](MACOS.md), [open work](ROADMAP.md), and the [native test checklist](NATIVE_ACCEPTANCE.md). The public source and articles are published. Hosted Mac follow-up checks now pass; the version-tag workflow produces platform evaluation archives.
