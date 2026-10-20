# 0.1.0b1 — first source beta candidate

Prepared for review; no public release has been published.

VISOR supervises independent Codex and Claude Code terminals: exact-session resume, verified Focus/arrangement, operator messages, approved session pairs and tracked wrap-up. Existing terminal windows remain usable independently.

The broker now waits for in-flight HTTP responses before exiting, preventing a truncated stop acknowledgment.

This candidate adds desktop shell PATH resolution for version-manager provider installations, shared Codex daemon startup verification, Linux title/closure fixes, and further macOS preparation. Mac launches explicitly carry the resolved PATH and source-mode PYTHONPATH into Terminal. Other Mac changes include restoring minimized Terminal windows, clearer refusal reasons, avoiding unintended Terminal relaunch during missing-window checks, and validating the app's Automation usage description before signing and after extraction.

macOS remains experimental. No live Mac build or desktop test has been completed. The CI definition covers Apple silicon and Intel; those jobs have not run for this candidate. Windows/Linux evidence and remaining checks are recorded in [STATUS.md](STATUS.md). Live Claude model validation is unclaimed.

Windows and Linux native beta archives may be published independently once their exact-revision checks, dependency-distribution review and release approval are complete. No macOS binary is represented as tested. Existing packages are unsigned on Windows/Linux; planned Mac packages use local ad-hoc signing, not Developer ID notarization.

See [ROADMAP.md](ROADMAP.md), [MACOS.md](MACOS.md), and [SECURITY.md](../SECURITY.md) before relying on this evaluation build.
