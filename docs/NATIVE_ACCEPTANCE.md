# Build and test on your Mac and Linux machines

Use a fresh checkout or a copy of the source including the new scripts and lockfile.
Do not copy Windows `.venv`, `build`, `dist`, caches, VISOR data or provider credentials.
Each machine needs its own native Python 3.12, uv, provider executables and provider login.
Linux build/extracted-package and bounded X11 adapter checks have passed on Ubuntu/KWin;
see [LINUX_ACCEPTANCE.md](LINUX_ACCEPTANCE.md). Full desktop/provider acceptance and
native macOS acceptance remain pending.

The current lockfile selects PySide6 6.12.0: its macOS wheel targets macOS 14+; Linux x86_64
targets glibc 2.34+ and Linux ARM64 targets glibc 2.39+. These are wheel requirements observed
in uv.lock, not native acceptance results. An older Mac/Linux system may require a separately
validated older Qt dependency selection. Do not bypass frozen resolution or reuse Windows wheels.

## Prepare and build

On macOS, use a native Python 3.12 matching the machine architecture. On Ubuntu/X11,
install the runtime prerequisites first:

```sh
sudo apt-get update
sudo apt-get install xterm wmctrl libegl1 libopengl0 libxcb-cursor0
```

For a visible provider-free Linux/X11 window check, also install `x11-utils` and run
`.venv/bin/python scripts/verify_linux_windows.py`. It opens two disposable terminals,
tests exact focus/placement/closure and worker exit, then closes only those terminals.
It does not replace the full interactive checklist below.

On either host, from the repository root:

```sh
python3.12 scripts/native_build.py
.venv/bin/python scripts/run.py --doctor
.venv/bin/python scripts/run.py --data-dir .local/native-acceptance
```

The build command bootstraps the locked environment, runs tests/lint, renders the source UI,
builds on the host, then extracts and verifies the host archive, including executable modes,
resources, relocation to a path containing spaces, broker restart/queue durability, singleton
activation and simulated Claude hook/MCP delivery. It does not call a provider model.
Install uv first if bootstrap reports it missing. If your Python command differs, use that
Python 3.12 command. `--skip-install` reuses a prepared environment; `--offline` needs a populated cache.

## Run the built deliverable

Archives and adjacent SHA-256 files are in `dist`. Windows uses ZIP; macOS/Linux use `.tar.gz`
to preserve executable permissions and, on macOS, framework symlinks. Extract the entire archive.

- macOS: open `visor.app` in Finder. Terminal automation will require OS permission. Verify
  both Python and the packaged app because their Automation identities differ. The app has
  a local ad-hoc signature, not Developer ID signing/notarization; a downloaded build may be
  blocked by Gatekeeper. Prefer building locally for this acceptance run. Console diagnostics:
  `visor.app/Contents/Helpers/visor-console.app/Contents/MacOS/visor-console --doctor`.
- Linux: run `visor/visor` from the extracted directory. Keep the whole `visor` directory.
  Run `visor/visor-console --doctor` for diagnostics.
- Wayland: xterm needs XWayland. Focus, arrangement and managed-window closure are intentionally
  unavailable. Use an X11 login session for full window-control acceptance. Do not mark Wayland
  parity passed because XWayland can open a terminal.

Use throwaway provider sessions in a disposable project for deletion tests. Provider discovery
still reads your normal provider history even with a separate VISOR `--data-dir`; that argument
isolates VISOR state, not provider storage.

## Interactive acceptance checklist

Run each applicable case in source and from the extracted package. Capture exact failures;
do not mark a case passed when it is unsupported or skipped.

| Case | Expected result |
| --- | --- |
| Startup | UI appears, can resize/move/minimize, actions remain accessible at native DPI |
| Second launch | Existing supervisor activates; no duplicate UI for the same data directory |
| Provider discovery | Installed providers list sessions; one missing provider does not block the other |
| New/resume | Exact session/project starts in a separate terminal; paths with spaces/quotes work |
| Ownership | Duplicate workers refused; stale/ambiguous/permission-denied identities cannot control another terminal |
| External Focus link | Explicitly choose/confirm a terminal window, focus it, change/close it and verify stale identity rejection; unlink before Resume. It cannot grant arrange/delete privileges or select a provider tab |
| Focus/arrange | Selected managed window only; restored layouts clamp to connected monitors |
| macOS tabs | Deletion refuses a managed window with extra tabs; unrelated tabs survive |
| Messaging | Operator receipt and completion are distinct; held agent messages stay held after re-enabling |
| Wrap-up | Per-session wait control, completion, elapsed time and unresolved observed work are visible; exceeding wait never kills work |
| Leave/reopen | Broker and terminals survive leaving the UI; reopening reconnects |
| Broker restart | Helper reconnects, held/uncertain queue states persist; live ownership remains protected |
| Disposable deletion | Verified terminal closes, observed workers stop, exact history disappears; adjacent session/project remain |
| Close refusal | Prompt, Automation denial or unproven worker exit preserves history for retry |
| Reboot | Reopen with stale state; no duplicate sessions or silently retried uncertain deliveries |
| Accessibility | Keyboard navigation, labels/tooltips, scaling and multi-monitor placement are usable |

Live Claude model testing remains waived. Record synthetic Claude checks separately from any
optional runtime test. Codex peer tools on already-loaded threads remain a known limitation.

## Report results

Copy [NATIVE_TEST_REPORT.md](NATIVE_TEST_REPORT.md) into `.local/` and fill it in. Include the Git
revision and `--doctor` output after removing home/project paths or other personal information.
Share build errors and the completed checklist; never include credentials or private transcripts.
Results update `ROADMAP.md` and `STATUS.md` before any platform support claim changes.

## 1.3.0 desktop acceptance additions

These are pending manual acceptance unless a dated platform receipt explicitly records a pass. Mocked launch commands and offscreen Qt tests are separate evidence.

- Windows: exit the old supervisor, launch the candidate, confirm visible version/build/path in About. Launch another version against the same data directory and verify an older-running-window notice, not a silent upgrade. Keep existing consoles running during the supervisor-only upgrade.
- Minimize a disposable managed Windows console, double-click its session row and verify the exact console becomes foreground. Repeat after sorting, Alt+Tab, on each monitor and at mixed DPI (100/150/200 percent). Check an externally linked console separately; verify selected tabs manually.
- Reboot/crash recovery: use disposable sessions and unresolved messages, restart and inspect ownership, deduplication, uncertain delivery, reconnection and explicit-stop behavior. Do not treat a receipt as proof all detached work stopped.
- Type an unfinished operator draft, change its recipient, restart the supervisor and verify both. Remove that recipient and verify the draft cannot silently target another session.
- New terminal appearance: Ubuntu/xterm custom monospace font at 12/16 points; Windows existing named/GUID profile with distinct font; Mac existing named Terminal profile with distinct font. Check cwd, literal arguments, launch identity, Focus and close protection remain correct. An unavailable Mac profile must fail before launching a provider.
- Backup/recovery: private paths and ACLs, snapshot during activity, retention, deliberate offline restore with held/uncertain sends, old credentials invalidated and external links cleared. Refuse restore while broker/UI/helpers remain active. Never use real provider-history deletion for this fixture.

## Prism 1.5.0 review

Use disposable boards and records for destructive checks.

1. Open two Prism boards. Verify one resizable workspace, wide previous/next targets, readable connected names, minimize/maximize and no focus stealing during ordinary updates.
2. Hide one board, update it from its session and restart VISOR. It remains dismissed until an operator explicitly reopens it. Close or delete a board without stopping its provider sessions.
3. Verify secondary card/board actions start closed; question input and declared direct controls remain usable. Resize gadgets: buttons, labels and charts never overlap. Check unsent text, sketch, reply and control retries through refresh/restart.
4. Select grid rows by click, Ctrl/Cmd, Shift and Ctrl/Cmd+A, then refresh/sort/filter. Bulk actions target the retained visible selection. Delete defaults No and shows the selected count; partial failures are reported.
5. Render the rich component catalog, safe HTML, emoji and PNG/JPG. Try both themes, keyboard focus, scaling and layout undo/redo/snap. A display or input receipt never implies task completion or approval.
6. Open the authenticated browser view. Review desktop canvas and narrow phone/tablet reflow, keyboard/touch controls, reduced motion and light/dark themes. Retry a lost input receipt with the exact saved request. Verify unauthenticated/cross-origin access is refused.

Local synthetic Chrome viewport checks do not replace physical phone/tablet,
screen-reader or native Windows/Mac acceptance.

## 1.5.0 table/agent add-on

- Render `examples/prism/styled-project.json` in both themes. Verify colored/thick/dashed borders, header/row/cell overrides, badges and literal tooltips.
- Sort numeric cells, filter rows, select with modifiers and copy selected visible rows. Update/reorder data using stable row IDs; selection, widths, filter, sort and scroll persist without emitting input events.
- Shrink a native card with a gadget, direct action, stale cue and help. Its full content scrolls into view; controls and graphics remain separate. Check the browser at phone/tablet widths and in canvas mode.
- Open the plain help text; cycle ready/loading/empty/error/stale metadata. Source/time is optional detail; no fresh/verified result is invented. Invalid and out-of-range timestamps fail cleanly.
- Use a session credential through real MCP stdio and CLI: discover schemas/presets, preflight without writes, publish/update with exact revision/receipt, receive typed input and acknowledge read. Wrong ownership, changed access, invalid assets/styles and actor forgery are refused.
- Review the optional service's Start/Stop, default-off state, private links, fixed routes and broker shutdown behavior against `WEB_ACCESS.md`.
- With the GUI-owned web server active, close VISOR and relaunch: one prompt offers the exact previous IP/port, defaults No and starts no socket before the answer. No stays off; Yes starts with a new token and does not launch a browser. Stop before exit suppresses the reminder. Binding failure stays off. CLI-owned listeners remain independently managed; smoke mode skips prompting.
