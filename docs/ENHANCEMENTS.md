# VISOR 1.3.0 enhancement checklist

This checklist records the 1.3.0 beta candidate's local validation on 2026-10-22. Hosted Windows/Mac CI and interactive platform acceptance are not established by local Linux tests.

| Review item | Candidate implementation | Remaining acceptance |
| --- | --- | --- |
| Message selection changes after refresh | Preserve selected message IDs across insertion, removal and polling; deleted selections clear | Regression included |
| Old supervisor silently activated by a new download | Visible UI version; About with build, executable, broker and provider diagnostics; version-aware activation with legacy fallback | Native Windows/Mac IPC and desktop checks |
| Broker failure needs manual recovery | Automatic reconnect with capped backoff; explicit Stop stays stopped; stale snapshots cannot overwrite a new connection | Crash/reboot and Windows service interactions |
| Interactive Windows acceptance | Expanded repeatable checklist in NATIVE_ACCEPTANCE.md | Windows foreground, minimized windows, multiple monitors and DPI still require a desktop |
| Unfinished operator message lost on restart | Debounced durable draft with recipient; serialized writes; close flush and save-failure warning; missing recipient requires explicit choice | Native desktop restart acceptance |
| Backup/restore | Private consistent SQLite snapshots, bounded retention, opt-in pre-upgrade backup; offline recovery with queue holds and credential/link reset | Windows ACL and unusual-filesystem acceptance |
| Terminal appearance | xterm font/point size; named Windows Terminal and macOS Terminal profiles for new consoles | Native Windows/Mac launch/profile checks |
| Documentation | Current downloads, appearance, recovery and actual validation limits described | Public push/release approval |
| Build cache and disk space | Space preflight; explicit cleanup preview/apply preserving selected/running/recent bundles | Continue measuring native packaging headroom |

Terminal appearance changes apply to new console windows. Windows/Mac profile fonts are configured in the terminal application's own profile settings. Existing sessions and console settings are left in place.

Keep native Wayland controls, live Claude model/channel acceptance, detached-work detection limits and dependency-distribution review on the broader [roadmap](ROADMAP.md).
