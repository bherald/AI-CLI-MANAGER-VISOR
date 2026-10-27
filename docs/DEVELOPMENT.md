# Development and tool requirements

Use Python 3.12 and uv. The source application uses PySide6-Essentials, websockets and psutil;
storage and the loopback broker use Python's standard library. PyInstaller creates native
bundles. pytest and Ruff are development tools. Dependencies are installed in the local
`.venv`, with downloads retained in `.cache/uv` and versions recorded in `uv.lock`.

## Windows

For the complete local build pipeline, run:

```powershell
py -3.12 scripts/windows_build.py
```

This resolves `uv.lock` if absent, installs into `.venv`, runs tests and lint, renders the
UI offscreen, builds the Windows executables, smoke-tests the packaged UI, and writes an
unsigned beta ZIP plus SHA-256 checksum under `dist`. It does not modify machine-wide
proxy settings or provider authentication. Use `--offline` only with a populated package cache.
The Windows wrapper delegates to `scripts/native_build.py`, which now also verifies the extracted
archive, singleton activation and simulated Claude broker/hooks/MCP. Use `--skip-install` only
with the locked environment already installed. The result requires the acceptance checks in STATUS.md.

Individual steps:

```powershell
py -3.12 scripts/bootstrap.py
.venv/Scripts/python.exe scripts/check.py
.venv/Scripts/python.exe scripts/run.py --doctor
.venv/Scripts/python.exe scripts/run.py --data-dir .local/visor
.venv/Scripts/python.exe scripts/build.py
.venv/Scripts/python.exe scripts/verify_windows_package.py
```

Windows Terminal must provide `wt.exe`; providers must be native executables on PATH.
The package verification command checks the ZIP checksum, extracts into an isolated cache
folder, renders the extracted UI, and verifies packaged broker restart and queue persistence.
It neither launches nor messages a real provider; it is not a clean-machine acceptance test.
No administrator rights are needed by the application. Native focus requests can be refused
by Windows foreground activation rules. Live acceptance must test this behavior.

## Ubuntu and macOS

```sh
python3.12 scripts/bootstrap.py
.venv/bin/python scripts/check.py
.venv/bin/python scripts/run.py --doctor
.venv/bin/python scripts/run.py --data-dir .local/visor
.venv/bin/python scripts/build.py
```

Ubuntu uses Konsole (preferred) or xterm for separate windows, and wmctrl for X11 focus/arrangement. Wayland keeps
launch/resume and the supervisor available, with window controls unavailable. Qt may need
distribution runtime libraries, including EGL/OpenGL and xcb-cursor. On macOS, Terminal.app
automation needs the user's OS permission; test the packaged app identity as well as Python.

For the full pipeline on either host, use `python3.12 scripts/native_build.py`.
See [NATIVE_ACCEPTANCE.md](NATIVE_ACCEPTANCE.md) for exact setup and interactive tests and
[NATIVE_TEST_REPORT.md](NATIVE_TEST_REPORT.md) for recording results. The prepared source
handoff archive can be copied between hosts; virtual environments and native binaries cannot.

## Reproducibility and packaging

Dependency ranges are currently in pyproject.toml. Bootstrap creates `uv.lock` on the first
successful resolution and then uses `uv sync --frozen --extra dev`; CI follows that path.
A resolved `uv.lock` is present; include it when initializing the repository.

Build on each target OS. The Windows output is an unsigned portable directory with GUI and
console helper entry points; Linux has the same directory structure; macOS also creates an
app bundle containing a console helper. macOS/Linux archives use tar.gz to retain executable
modes and symlinks. macOS includes the app's docs/licenses/manifest, uses a local ad-hoc signature
after assembly, verifies that signature, and smoke-tests the embedded helper. Native macOS
packaging and the remote CI matrix remain unverified. The Linux pipeline
has passed locally on Ubuntu 24.04 x86_64; see [LINUX_ACCEPTANCE.md](LINUX_ACCEPTANCE.md)
for its bounded native evidence and remaining desktop/provider acceptance.

The GitHub workflow runs isolated tests, lint, offscreen startup, builds, and packaged startup.
It retains build artifacts only on manual workflow dispatch. It neither creates a GitHub
release nor publishes the repository. Signing, notarization, dependency notices, distribution
installers and clean-machine checks remain release gates. MIT is the project license. The build script
collects installed distribution license/notice files, writes a dependency manifest, and packages
the Windows executable folder in a versioned ZIP; the result still requires notice review.
The `third_party` snapshot supplies missing Qt/PySide license and attribution files from pinned
official source commits. Builds validate its SHA-256 hashes and runtime version without fetching.
`native-binaries.json` records actual binary paths/hashes/sizes; macOS hashes are labelled pre-signing.
Update the snapshot with `scripts/collect_qt_notices.py` only after reviewing a dependency upgrade.
This does not complete source/replacement or platform redistribution review.
Use `python scripts/verify_native_package.py` to verify the host archive after extraction,
including resources, executable modes, relocation, broker restart and provider-free integration tests.
Each build uses a fresh `dist/native/<id>` staging directory so PyInstaller never cleans a running
installation. Archives/checksums remain directly under `dist`; `latest-build.json` points to the output.

Authentication for real Codex/Claude tests stays in each provider's supported login flow.
Do not use paid model calls in ordinary CI. Supply Windows, Ubuntu/X11, Ubuntu/Wayland, and
macOS test machines for final interactive acceptance. Live Claude model testing is waived by
the operator; maintain documented CLI/hooks/channel contracts using the isolated tests instead.
Do not run `verify_claude_mcp.py` as part of the required workflow. That optional older live
probe is retained for future opt-in use and is not ordinary CI.

The Claude tests use synthetic transcript/roster fixtures and a real isolated broker, MCP
process and hook process. They cover channel notifications, cross-provider policy, completion,
and broker restart without starting a provider model. See `integrations/claude/README.md` for
the inbox/local-preview/approved-plugin routes. Native `claude agents --json` discovery is
version-gated to 2.1.290+ to avoid unknown subcommands becoming prompts on older versions.


For read-only loading diagnostics, run `visor-console.exe --diagnose-loading`. This uses the
real broker/provider metadata and offscreen UI, prints row/recipient counts, and saves a local
`ui-smoke.png` in the selected data directory. It does not launch, resume, or message agents.

Run `scripts/verify_single_instance.py dist/visor/visor.exe` to check two packaged UI launches
using isolated cache data and no provider on PATH. The second launch must exit while the
first stays alive. The test terminates only its own test UI and stops its isolated broker.

Run `scripts/verify_claude_package.py dist/visor/visor-console.exe` to exercise the packaged
broker, hook and MCP/channel entry points with a simulated Claude client. It checks scoped
cross-provider routing, explicit completion, held messages and reconnection after restart.
It never starts a provider model and uses only isolated cache data.

The extracted-package verifier also runs `scripts/verify_upgrade.py <console-helper>` to keep a
synthetic worker alive across broker relocation. Use `--previous-executable <old-console-helper>`
to check an actual earlier build. Both installations must be trusted local builds; the test uses
isolated data, stops only its own fixture processes, and makes no provider model calls.

New Windows data directories are protected before credentials are written. Existing directories
are checked without permission changes, so old broad portable directories may now be rejected.
Use a new private directory or deliberately restrict the existing folder's ACL in Windows.
Do not change project/home/drive-root permissions to make a portable data directory pass.

With VISOR and its broker closed, use `visor-console --maintain-storage --retention-days 30 --compact`
for offline storage maintenance. The broker lock prevents concurrent broker/maintenance ownership.
Unresolved messages and deduplication records remain; no provider transcript is removed.

`python scripts/audit_public_repo.py` checks source candidates and reachable Git blobs for common
credential patterns and sensitive filenames, printing locations without secret values.
`python scripts/source_bundle.py` additionally builds a source handoff without `.git` or ignored data.
Review [RELEASE_PROCESS.md](RELEASE_PROCESS.md) before the eventual bherald repository.

## Operator-reply protocol and existing-session fallback

The fixed operator `message_get` and agent `operator_reply` contract is documented
in [ARCHITECTURE.md](ARCHITECTURE.md). Source tests use temporary stores and an
in-memory invocation of the real HTTP handler; they never address a live provider.
Run the focused source tests with the existing environment:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_operator*.py'
```

Both ordinary loopback RPC requests and non-Prism MCP frames have a 65536-byte
limit, including the complete serialized JSON envelope (and the MCP newline).
The 16000-code-point body bound is separate: a literal UTF-8 body of 16000 emoji
can fit, whereas ASCII-escaped JSON for that same body exceeds the byte limit.
Clients must measure the bytes of their actual serialized request; neither a
JavaScript UTF-16 length nor a smaller guessed character limit is equivalent.
Oversized framing is explicitly rejected before the operation, with the original
draft retained. No truncation, implicit content rewrite, larger global limit or
new request identity is an acceptable recovery. Unknown outcomes require the exact
same parent, canonical body and request UUID; a retry cannot replace an accepted
final reply.

A currently running recipient without the new tool may use the updated installed
MCP executable with **its own existing agent credential file**:

```text
visor-console --mcp --credential <this-session-existing-agent-credential.json>
```

Use that invocation through the provider's supported MCP attachment/discovery
route, or its ordinary stdio initialization/tools/list/tools/call exchange. Do not
restart a conversation to attach it. The file must already belong to this exact
recipient and authenticate as role `agent`; helper or activity files cannot reply.
MCP passes its explicit token to Client and never uses the broker operator token.
No credential minting, copying, borrowing or logging is part of this fallback.

An already authorized scoped Python caller can use the same identity explicitly:

```python
import json
from pathlib import Path
from visor.broker import Client

config = json.loads(own_existing_agent_credential.read_text(encoding="utf-8"))
if not isinstance(config, dict) or not isinstance(config.get("token"), str) or not config["token"].strip():
    raise ValueError("Existing agent credential is missing or invalid; hold the reply")
client = Client(Path(config["root"]), token=config["token"])
reply = client.call("operator_reply", message=parent_message_id,
                    body=pinned_canonical_body, request_id=pinned_request_uuid)
```

Keep the token in memory. Never substitute `Client(root)`: omitting the explicit
token selects the local operator credential and violates this fallback's identity.
The installed broker must support the operation; missing tool/broker capability
means hold the draft, not use a broader send or operator credential. Tool inventory
alone does not establish running-recipient availability. The designated runtime
owner must separately prove supported attachment and one bounded real recipient
ACK plus explicit reply before claiming live two-way acceptance. Source tests and
prepared commands do not claim that runtime step has happened.

## Optional VISOR and Prism web surface

`visor-console --web` (alias `--prism-web`) serves the authenticated Sessions and
Prism views. It defaults to loopback and a free port; an explicit `--web-host`
chooses a specific LAN/VPN IP and `--web-port` retains a browser origin across
restarts. The running VISOR broker supplies data through fixed APIs. The native
Web access dialog supplies Start/Stop controls without automatic startup.

Static HTML/CSS/JavaScript is packaged through setuptools data and PyInstaller
resources on each desktop OS. Source browser checks use an installed Chrome
with a temporary profile and synthetic state, and skip that check when Chrome
is unavailable. `scripts/verify_prism_package.py PATH-TO-CONSOLE` validates the
frozen combined assets, scoped APIs, fixed send/receipt routes and exact retries.
It is included in extracted native-package acceptance and makes no model calls.

The current authoring contract revision is exposed through `activity_catalog`; `activity_validate` uses the runtime validator plus read-only revision/ownership/asset checks. Tests exercise actual scoped MCP stdio and CLI processes. See [agent contract](PRISM_AGENTS.md), [web operations](WEB_ACCESS.md) and `tests/test_activity_authoring.py`.
