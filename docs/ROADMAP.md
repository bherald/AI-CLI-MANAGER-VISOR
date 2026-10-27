# Beta roadmap and known limitations

## Before downloadable beta releases

- Run the native pipeline for each exact Windows/Linux release revision; retain archive checksums and platform results.
- Review the actual bundled libraries, required notices, Qt source/replacement route and platform redistribution obligations. The MIT source license does not cover all dependencies.
- Record clean-machine prerequisites and unsigned-package expectations. Signing, installers and architecture coverage remain separate work.
- Configure private vulnerability reporting when the GitHub repository is created.

A source beta can be published with accurate limits while these binary gates remain open. Windows and Linux downloads can be approved independently of macOS.

## macOS experimental port

The remaining native work is listed in [MACOS.md](MACOS.md). Host builds and AppleScript compilation can run on GitHub-hosted Macs after the repository is approved and pushed. Interactive acceptance needs a Mac desktop. A green hosted build does not establish Automation permissions, Finder launch, Focus or safe deletion.

## Functional follow-up

- Codex: full MCP peer tools can be absent from already-loaded conversations. Operator queue messages and explicit receipts use a separate path.
- External terminals: confirmed native window links provide Focus; provider/tab ownership and destructive controls remain unavailable.
- Wrap-up: receipts and observed descendants do not prove every detached process or scheduled task has stopped.
- Storage/ownership: expand crash, reboot and unusual-filesystem acceptance while preserving conservative identity checks.
- Claude: verify live behavior where available; channel approval and organization policy remain provider prerequisites.
- Accessibility, keyboard navigation, multiple monitors and DPI need broader desktop testing.
- Wayland Focus, arrangement and managed closure remain unavailable through the X11 adapter.

Use disposable sessions for destructive tests. Never interpret a skip, simulated response or transport acceptance as a live completion result.

## 1.3.0 review follow-through

The accepted review/enhancement list, including terminal fonts and profiles on all three platforms, is tracked in [ENHANCEMENTS.md](ENHANCEMENTS.md). Message identity, upgrade visibility, reconnection, drafts, private backups/recovery, appearance controls and build-space checks are implemented in the candidate; platform acceptance limits remain explicit. Consult the versioned release notes for the exact published source revision and native-build results.

## Prism and optional web 1.5.0 acceptance

The single workspace, rich components and combined VISOR/Prism browser feature
are implemented. Native Windows/Mac, real Android/iPhone/iPad, screen readers,
scaling and unusual display/network configurations still need acceptance.
Browser scope is session status, recent messages, operator messaging/receipts and
Prism; terminal lifecycle/admin stays in the desktop. HTTPS gateway configuration
and public hosting remain operator-managed and are not enabled automatically.
