# Linux build and acceptance — 2026-10-20

Host: Ubuntu 24.04.5 LTS, x86_64, glibc 2.39, KDE/KWin X11, Python 3.12.3.
Baseline revision: `a49f319`; this checkout adds the Linux fixes below.
Recorded pre-beta build identity: `2026-10-20-linux-2`, so reopening replaces an older broker
through the existing upgrade path rather than retaining its old window-control code.
Locked environment: PySide6/Qt 6.12.0, PyInstaller 6.22.3. Installed prerequisites:
`xterm`, `wmctrl`, `libegl1`, `libopengl0`, `libxcb-cursor0`.
The visible window probe also uses `xprop` from `x11-utils`.

## Fixes established on this host

- xterm now disables application title changes with the `allowTitleOps` resource.
  A synthetic provider emitting an OSC title override previously replaced the
  managed title, breaking exact-window lookup. The original launch token now survives.
- Close verification retries the observed `wmctrl -l` / X11 `BadWindow` race
  while the terminal disappears. An enumeration error never proves closure;
  verification still requires a successful list without the target window.
  Other errors remain failures and the existing close timeout remains bounded.

## Validation

Run the complete locked source/build/extracted-package checks:

```sh
python3.12 scripts/native_build.py
```

The native Linux pipeline passed: core tests, Ruff, source and packaged offscreen
rendering, archive checksum, binary/notice integrity, Unix executable modes,
relocation into a path with spaces, broker restart and queue persistence,
singleton activation, simulated Claude hooks/MCP/channel, and helper survival
across installation relocation. These checks make no provider model calls.
That pre-beta suite ran 142 tests successfully (four Windows-only checks skipped). Current beta results are recorded separately in STATUS.md.

The visible native adapter check is separate:

```sh
.venv/bin/python scripts/verify_linux_windows.py
```

It passed on KWin/X11 using two disposable xterm windows and synthetic workers:
quoted project paths/arguments, provider title override protection, mismatched
identity rejection, geometry/placement, exact active-window Focus, external
Focus validation and stale-window rejection, verified closure/worker exit,
and adjacent terminal/worker preservation. It moves/focuses only its test windows
and writes local evidence under `.cache/linux-window-acceptance/`.
Wayland is explicitly refused by this test.
The intermittent close-race regression also passed five consecutive native runs.
Source and packaged UI rendering additionally passed with `QT_QPA_PLATFORM=xcb`
on the actual desktop; both synthetic empty-session screenshots were visually
inspected. The packaged broker also reported the expected Linux build identity.

Build logs and screenshots stay in ignored `.cache/`. Native artifacts are in
`dist/visor-0.1.0a1-linux-x86_64.tar.gz` and its adjacent SHA-256 file;
`dist/latest-build.json` identifies the current unpacked bundle.

## Still required

This is an unsigned Linux evaluation build. The checks above do not establish
full provider or desktop acceptance. Real provider new/resume, operator receipt
and wrap-up, actual disposable provider-history deletion, reboot recovery,
multi-monitor/DPI/accessibility, clean-machine dependencies and Wayland remain
open. Only a single X11 monitor was available. Claude is absent on this host;
its runtime model testing remains waived and its isolated integration checks pass.
Codex 0.163.0 was detected by version only; no real session was launched or messaged.
macOS, signing, installers and redistribution review retain their existing gates.
