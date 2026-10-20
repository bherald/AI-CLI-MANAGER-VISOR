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
