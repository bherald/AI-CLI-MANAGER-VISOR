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
