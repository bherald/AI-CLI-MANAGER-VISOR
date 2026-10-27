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
4. Click **Start**, then **Open VISOR** or **Open Prism**. **Copy link** supplies
   the private VISOR link for the intended browser/device.
5. Click **Stop** when finished. It stops this GUI-owned listener and revokes its
   private link. Starting again issues a new token.

Closing the Web access dialog or native Prism window leaves a started listener
running. Exiting VISOR stops its GUI-owned web server. Restoring a workspace or
opening this dialog does not start web access.

## Restart consent

If web access was still on when VISOR closed, the next normal launch asks
**Start web access again?** It shows the previous specific bind IP and port;
**No** is the default. Choosing **Yes** starts a fresh listener on that offered
address/port, with a new private link/token. It does not open a browser for you.
Use **Web access…** to open or copy the new link.

Choosing **No** leaves web access off and clears the reminder. Explicitly stopping
web access before exiting also clears it, so the next launch does not prompt.
A failed restart is reported and leaves the server off. The prompt appears once
per normal launch; reconnects do not repeat it, and smoke-test mode never asks.

The saved state is an offer, not automatic network startup. A new manual dialog
while the server is off still defaults to loopback. Existing tokens/URLs are not
reused, and the independently started CLI server has its own process/lifetime.

For mobile access, the device must reach the selected LAN/VPN address. This does
not require exposing the VISOR broker. Protect remote traffic using a trusted
network/VPN. The built-in listener uses HTTP and does not configure TLS.

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
| Unauthorized/locked page | Reopen the latest private link after restart/Stop; an older token is revoked |
| Data unavailable | Keep the matching VISOR broker running and check its diagnostics; the standalone web command does not replace/start the broker |
| API refused through a proxy | Verify exact Host/Origin handling; do not disable the server's checks |
| Saved send/action unconfirmed | Retry the original saved request; do not generate replacement request IDs |
| Restart asks about web access | It was on at the last GUI shutdown; No is default. Use Stop before closing to suppress the next reminder |
| Restarted service has a different private link | Each start rotates its token; open/copy the current link from Web access |
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
