# macOS experimental port

**Hosted Apple silicon and Intel builds and extracted-package acceptance now pass. Interactive Mac desktop acceptance remains unverified.** The [successful follow-up jobs](https://github.com/bherald/AI-CLI-MANAGER-VISOR/actions/runs/39462649997) include source tests, native AppleScript compilation, offscreen UI startup, helper/app signature checks, broker restart, simulated provider transport and relocation/upgrade checks. These results do not establish Finder, Automation permissions or live window-control behavior.

## Implemented and checkable without a Mac

- Terminal.app launch passes the project path and helper command as quoted arguments; session text is not interpolated into AppleScript source.
- Native operations match a unique managed tab title and revalidate the saved window ID. Missing and ambiguous windows fail conservatively.
- Focus restores a minimized window. Deletion refuses windows with additional tabs, then waits for verified window and observed-worker exit before deleting history.
- Missing-window checks avoid starting Terminal as a side effect. Automation/association failures remain failures, rather than evidence of closure.
- Finder launches load PATH from the interactive login shell, with a bounded timeout and inherited-PATH fallback. The Terminal launch explicitly forwards PATH and source-mode PYTHONPATH so an already-running Terminal cannot silently restore an older search path. Helpers do not repeatedly read shell startup files.
- The app includes `NSAppleEventsUsageDescription`, verified before ad-hoc signing and after archive extraction. Apple requires this description for apps sending Apple events: [Apple documentation](https://developer.apple.com/documentation/bundleresources/information-property-list/nsappleeventsusagedescription).
- The bundle contains a console-enabled `visor-console.app` under `Contents/Helpers`, plus resources and notices. PyInstaller BUNDLE separates helper resources from code directories so dotted metadata folders are not treated as nested code bundles. Archives preserve executable permissions and symlinks; the verifier checks the assembled signature and extracted resources.

The automated tests cover command quoting, refusal reasons, extra-tab safeguards, minimized-window restoration, PATH fallback and bundle metadata. Native Mac CI additionally compiles generated AppleScript without executing its window-control operations.

## First hosted CI and follow-up

The [first public run](https://github.com/bherald/AI-CLI-MANAGER-VISOR/actions/runs/39448456796) passed Windows/Linux but failed on both Mac architectures:

- Apple silicon passed source tests and AppleScript compilation, then codesign rejected a dotted `.dist-info` directory inside the copied console onedir. The helper is now built as a proper console-enabled app bundle, verified and signed before the enclosing app. It retains stdin/stdout for broker/MCP use. Both GUI and helper resolve the same nested executable.
- Intel broker tests exhausted their four/five-second startup limits while their processes were still alive. The test harness now waits up to 30 seconds for an actual ping, reaps its disposable process on failure and reports captured diagnostics. The longer wait still failed. The broker now avoids HTTPServer's reverse-DNS lookup when binding its loopback-only endpoint; Intel broker and packaged acceptance tests then passed. Assertions remain enabled.

Apple documents the dotted-directory issue in [Placing content in a bundle](https://developer.apple.com/documentation/bundleresources/placing-content-in-a-bundle). The follow-up does not disable signing or skip Mac tests. The second follow-up also updated the upgrade fixture to copy the complete helper app rather than assuming a Windows/Linux `_internal` directory. Signing and packaged-helper checks now pass on both architectures; interactive testing still needs a Mac desktop.

## Build on a Mac or hosted Mac

The locked Qt wheels require macOS 14+; use a native Python 3.12 of the intended architecture. Install uv, then run:

```sh
python3.12 scripts/native_build.py
.venv/bin/python scripts/run.py --doctor
```

The workflow in `.github/workflows/ci.yml` includes `macos-14` (Apple silicon) and `macos-15-intel`. After an approved GitHub push, inspect each job independently. Manual and version-tag Actions runs retain downloadable evaluation archives for seven days. It does not create a public Release.

GitHub supplies hosted Mac machines; nothing needs to be cloned or installed to emulate a Mac locally. See [GitHub runner documentation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). PyInstaller builds are specific to the host OS: [PyInstaller documentation](https://pyinstaller.org/en/stable/operating-mode.html). We cannot produce a verified Mac app from this Linux build host.

## First interactive test

Use a disposable project and provider conversations. A separate VISOR data directory does not isolate provider discovery/history.

1. Extract the entire archive. Open `visor.app` from Finder, then test its embedded `Contents/Helpers/visor-console.app/Contents/MacOS/visor-console --doctor`.
2. Confirm correct provider path/version with NVM or another version manager. Test quoted paths and a project directory containing spaces.
3. Allow the app's Terminal request under System Settings > Privacy & Security > Automation. Source Python and the packaged app can have different permission identities. A denied request must show an actionable error and preserve sessions.
4. Test new/resume, provider lifecycle, operator read/completion receipts and supervised wrap-up. Do not infer success from a visible terminal alone.
5. Minimize a managed terminal, then Focus it. Test arrangement with more than one window and monitor, without touching unrelated windows.
6. Add another tab to a disposable managed window. Deletion must refuse and preserve history. Test missing, renamed, closed and duplicate-title windows without guessing ownership.
7. After checking the exact disposable conversation ID, test deletion with a single-tab window. Verify only its linked terminal/observed worker exits and adjacent conversations survive.
8. Test closing/reopening VISOR, moving the installation, reboot recovery, native keyboard/accessibility and Retina/multiple displays.

Record failures in [NATIVE_TEST_REPORT.md](NATIVE_TEST_REPORT.md) and update [STATUS.md](STATUS.md) only with actual evidence.

## Distribution still pending

No Developer ID certificate, hardened-runtime/notarization validation, Gatekeeper acceptance or clean-machine result is claimed. The current Mac package design uses an ad-hoc signature. Do not instruct users to disable OS protection. Terminal permission attribution for the embedded helper still needs a real Mac check. Apple silicon and Intel outputs must each pass their own build and desktop checks before claiming support.
