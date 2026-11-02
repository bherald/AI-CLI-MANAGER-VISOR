# Optional VISOR and Prism web access

Web access is **off by default**. It is a separate browser surface in the desktop
application, with explicit Start/Stop controls. No operating-system service,
automatic startup task, firewall rule, public site or tunnel is installed.

The same server provides **Sessions** and **Prism**. Application-specific
workboards, decisions and workflow policies belong to their consuming application;
the generic VISOR web surface is delivered and operated with VISOR. Desktop hosts are Windows,
Linux and macOS; clients use a modern desktop, Android, iPhone or iPad browser.
Linux/Chrome automated checks do not establish acceptance on every real device.

## Start from the desktop

1. Open VISOR and choose **… → Web access…**.
2. Keep **Bind IP: 127.0.0.1** for this computer only. For another device, enter
   this computer's specific LAN or VPN IP. Wildcard addresses are rejected.
3. Choose a port, or leave **Automatic** for a free port. The desktop remembers
   the resulting port, so browser drafts/layouts can use the same origin later.
   The next inactive dialog still defaults to local-only binding.
4. Choose **New token each start** (the existing default), or **Saved static token**.
   Static mode creates and saves a strong token when first selected. **Generate**
   replaces it; the masked field accepts an override. Valid changes save when you
   finish editing, including when the dialog closes. No separate Save is needed.
5. Click **Start**, then **Open VISOR** or **Open Prism**. **Copy link** supplies
   the private VISOR link for the intended browser/device.
6. Click **Stop** when finished. It stops this GUI-owned listener. Per-start mode
   creates a new token next time; saved-static mode keeps the same token across
   Stop/start and ordinary application restart until you Generate or override it.

Closing the Web access dialog or native Prism window leaves a started listener
running. Exiting VISOR stops its GUI-owned web server. Restoring a workspace or
opening this dialog does not start web access.

## Restart consent

If web access was still on when VISOR closed, the next normal launch asks
**Start web access again?** It shows the previous specific bind IP and port;
**No** is the default. Choosing **Yes** starts a fresh listener on that offered
address/port, using your saved token mode. Per-start mode generates a new token;
saved-static mode keeps the same token. It does not open a browser for you.
Use **Web access…** to open or copy the private link.

Choosing **No** leaves web access off and clears the reminder. Explicitly stopping
web access before exiting also clears it, so the next launch does not prompt.
A failed restart is reported and leaves the server off. The prompt appears once
per normal launch; reconnects do not repeat it, and smoke-test mode never asks.

The saved state is an offer, not automatic network startup. A new manual dialog
while the server is off still defaults to loopback. The independent CLI server
has its own process/lifetime, but uses the same saved browser-token mode for the
selected VISOR data directory. Saving a token never starts a listener or grants
broker/agent access.

For mobile access, the device must reach the selected LAN/VPN address. This does
not require exposing the VISOR broker. Protect remote traffic using a trusted
network/VPN. The built-in listener uses HTTP and does not configure TLS.

## Saved token and rotation

Browser settings live separately in owner-private `web-access.json`, outside the
broker's generic settings and agent/helper credential tables. POSIX requires the
current owner and mode0600 (private directory); Windows checks the directory and
the existing secret file's ACL. Generate uses fresh cryptographic randomness.
Windows file identity uses creation time when comparing an opened descriptor
with its path: CPython 3.12 reports different `ctime` meanings for those two
queries. The read still checks the descriptor's raw change time before/after,
plus file identity, modification time, size, type and owner. Existing ACL,
symlink, atomic-save and token-revocation checks remain in effect. See the
[CPython path-stat implementation](https://github.com/python/cpython/blob/v3.12.10/Modules/posixmodule.c#L2015)
and [descriptor metadata implementation](https://github.com/python/cpython/blob/v3.12.10/Python/fileutils.c#L1036).
Overrides accept 1–512 URL/header-safe ASCII letters, digits and `. _ ~ -`;
spaces, controls, full URLs, bearer headers and other characters are refused.
Use Generate when you want a strong token.
Before save, rotation or listener creation, VISOR rejects values matching the
current private broker endpoint or an existing agent/helper/activity credential
using the normal authentication digest and one bound, read-only current-store
lookup. Rejection preserves the old browser file and active token and never
prints the candidate. Missing metadata in a new profile is allowed; malformed,
unsafe or unreadable existing credential metadata holds the change explicitly.
No store is created/migrated, no credential/history scan runs, and no credential
is sent to a service for this check. This checks known current credentials;
unpublished values existing only in another process's memory are not discoverable.

A successful Generate/override first saves atomically, then updates the active
GUI listener. The old bearer is rejected immediately; open/copy the current
private link to reconnect. A separate CLI listener rejects access when its saved
token file changes and must be restarted explicitly. A failed save leaves the
old file and active token intact and reports that the change was not saved.
Token values are absent from status/error messages and request logs. The private
link itself remains visible/copyable in this operator dialog as before.

SQLite state backups do not contain this separate secret file. Explicit state
recovery revokes saved browser access alongside old broker/agent credentials;
choose saved-static and Generate/override again after recovery. Recovery is not
an ordinary restart. Failed recovery before database replacement preserves the
saved browser file. Keep profile files private; no token settings are shipped
inside a release package.

## What the browser provides

| View | Available features |
| --- | --- |
| Sessions | Readable names/status, project labels, session connections, recent coordination messages, operator composer, exact receipts and recipient-authored final replies |
| Prism | Board navigation, rich components, questions, typed controls, safe text/images, responsive Read view and desktop Canvas view |
| Styled tables | Bounded colors/borders, header/column/row/cell formatting, badges/help, optional sort/filter/copy, stable row selection and column sizing |

Session messaging uses the existing VISOR queue and policies. Exact receipts
separate delivery, reading, completion and a final reply. Viewing a page does
not acknowledge a message or approve work. Provider terminal contents, terminal
lifecycle/admin operations and arbitrary broker RPC are outside this browser UI.

Phone/tablet layouts stack cards and panels; wide screens can use a freeform
canvas. Native and browser table styling use the same published JSON fields.
Local filters, sorts, selection, widths and layout do not change shared data.
Use `row_ids` when agents update table values or reorder rows.

## Private links, storage and retries

The private URL includes a separate web capability token in its fragment. The
browser removes the fragment from the visible URL and sends bearer headers for
fixed API requests. It never receives the broker credential. The link permits
private VISOR session/message access, operator messaging and Prism interaction;
share it only with intended operators. This is operator access, not a separate
multi-user account/role system.

**Lock** clears that page's token and displayed private data. Reopen a current
private link to reconnect. Browser drafts, pending requests and layout remain in
that browser's local storage. They are not a SQLite backup or cross-device sync.
Changing host/port, browser/profile or device changes that storage context.

Unconfirmed sends, replies and control events retain their exact parameters and
request ID. Retry the saved request to reconcile it. Clearing a local retry does
not retract an already accepted action. Reading an event does not execute or
approve it; the contributing agent remains responsible for its normal authority.

## Command-line mode

Start the normal VISOR application/broker first. From the installed package:

```sh
visor-console --web --web-host 127.0.0.1 --web-port 8765
```

On Windows use `visor-console.exe` (for example `./visor-console.exe` from the
extracted directory). Add `--data-dir PATH` to select the same portable data
directory as VISOR. `--prism-web` remains an alias of `--web`.

The CLI prints a private link and serves until Ctrl+C. A specific LAN/VPN IP can
replace loopback. A CLI-owned server is stopped through its own process; the GUI
dialog manages only its GUI-owned instance. Use a fixed port when continuity of
browser-local drafts/layouts matters. Do not publish or paste capability URLs
into issues, release notes, shared logs or source files.

## Network and HTTPS boundaries

The listener validates the exact Host/Origin, bearer token, fixed routes and
bounded JSON. It does not trust forwarding headers or expose general RPC.
An arbitrary public reverse proxy will not work unchanged: external HTTPS
Host/Origin values differ from the configured internal origin. A separately
managed gateway must authenticate its users, validate its public Host/Origin and
map the trusted request correctly. There is no automatic public-hosting or TLS
gateway configuration in this release.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Phone cannot connect | Choose the host's reachable LAN/VPN IP, not 127.0.0.1; verify the device's network route and operator-managed firewall |
| Start reports unavailable address | Use an IP assigned to this computer; a remote machine's address cannot be bound locally |
| Port already in use | Stop the listener that owns it, or explicitly choose another port; another port has different browser-local storage |
| Unauthorized/locked page | Use the current link; static mode retains it through ordinary restart, while Generate/override or recovery revokes old access |
| Data unavailable | Keep the matching VISOR broker running and check its diagnostics; the standalone web command does not replace/start the broker |
| API refused through a proxy | Verify exact Host/Origin handling; do not disable the server's checks |
| Saved send/action unconfirmed | Retry the original saved request; do not generate replacement request IDs |
| Restart asks about web access | It was on at the last GUI shutdown; No is default. Use Stop before closing to suppress the next reminder |
| Restarted service has a different private link | Choose Saved static token to retain it; per-start mode rotates, and changing IP/port still changes the URL |
| Same-named sessions | Select the readable project/provider label; internal IDs remain transport identities |

## Testing and remaining acceptance

Source tests exercise explicit optional startup/shutdown, auth/Host/Origin,
sanitized data, contained assets, exact retries, desktop/mobile reflow, keyboard
selection, table styles and safe help/status text. Native package acceptance runs
`scripts/verify_prism_package.py` against the extracted console executable.
Native Windows/Mac, physical Android/iPhone/iPad, screen-reader and broader
network/TLS gateway acceptance remain separate checks.

See [security](../SECURITY.md), [agent authoring](PRISM_AGENTS.md),
[Prism format](../PRISM.md) and [release evidence](RELEASE_1_5_0.md).

## Saved-token source acceptance and rollback

Synthetic-token checks cover generation/override/reopen persistence, exact reuse
through Stop/start and approved restart, no listener on save, active and separate
listener revocation, atomic-save failure, URL/header/control rejection, private
file permissions, explicit Windows file ACLs, broker/agent/helper isolation and
state recovery. Native Windows ACL execution and macOS desktop behavior still
need their target-platform acceptance; mocked ACL checks do not establish that.
The masked desktop dialog is also rendered offscreen with a synthetic profile.
Normal builds collect these modules through existing UI/console imports and
include this document. They do not collect the private runtime profile file.
The final integrated package must be rebuilt/verified before root upgrades it.

For rollback, root stops browser access before using the previous source/package.
Older builds ignore the separate saved-token file and use their per-start token
behavior. Preserve private profile files and their owner/ACL protection; do not
copy browser tokens into broker or agent credentials to retain an older link.
