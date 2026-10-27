# Prism boards

Prism is a shared visual workspace beside your Codex and Claude conversations.
All boards live in one resizable desktop window. Wide previous/next buttons and
the board picker switch boards. The compact connection strip shows readable
session names. **Board actions** hides/shows boards and opens the browser view.
**Card actions** contains secondary editing controls and details; direct declared
buttons and inputs remain beside their gadgets, in separate reserved rows.

Drag card title bars, resize corners, edit text or draw. **Save changes** shares
an edit. Hiding cards, hiding boards and closing Prism preserve content, drafts
and session work. **Export picture** saves the visible workspace as PNG. Layout
undo/redo, snapping and layer controls affect local placement only. Freeform
boards and connected diagram nodes remain supported; templates are optional.

The header is **Title - Updated: MM/DD/YYYY hh:mm am/pm** in the system timezone,
including daylight-saving rules. Shared revisions carry a broker-assigned update
timestamp; older documents and local previews fall back to the source file's
modification time. Opening or repairing a timestamped revision does not change its
displayed update time. Hover the heading for owner/revision/contributor details.

Expand **Board actions** for visibility/browser controls and the active board's
Open board, Show all cards, Tile cards, Reset layout, Hide all cards, Undo/Redo
layout, Bring forward/Send backward, Snap to grid, Save layout and Export picture.
Overflow actions remain accessible at narrow widths. The footer is one concise
line in 8pt type, with full status in its tooltip. Secondary card controls start
closed; an unconfirmed action exposes an exact-retry control.

## Close, reopen or delete a saved board

Use **Hide board** to dismiss one board; closing the Prism workspace dismisses its open boards. Content can continue to update quietly, including after restarting VISOR. A session can request reopening; that request appears in the Prism chooser without reopening the window. Choose **Prism** to open the workspace, then **Board actions → Show a hidden board…** (or **Show all boards**) to reopen a dismissed board. If no board is open, the Prism chooser can reopen one directly. An authenticated operator control request can also reopen it. Showing a window is not proof that you read its content.

Agents can inspect `view_state` from `activity_list` or `activity_get`: dismissal, presentation, the last presented revision, unshown updates and a pending reopen request. Keep the same board ID; do not create replacement boards to bypass an operator dismissal. Repeated reopen requests coalesce into one indicator.

From the main menu, choose **Manage Prism boards…**, select one or more saved boards and choose **Delete selected…**. Nothing is selected automatically. Confirm the named boards and revisions to delete their content, history, replies and membership, close its view and remove its local layout and drafts. Shared image assets and existing backups are retained. Codex and Claude sessions and conversations continue unchanged. This operator-only deletion is separate from closing a window; deleted board IDs cannot be reused by stale session requests.

If the deletion result is uncertain, **Retry same deletion** reuses the saved request. If the board changed, refresh and review the new revision before confirming again.

## Give a running session access

Choose **Enable Prism access** from that session's menu. This creates a private, session-scoped Prism credential. It does not restart the session, change global provider settings, or grant supervisor controls. Use the console executable from this VISOR installation:

```sh
visor-console --activity list --session codex:SESSION-ID
visor-console --activity publish --session codex:SESSION-ID --document board.json --request-id unique-publish-1
visor-console --activity focus BOARD-ID --session codex:SESSION-ID --request-id unique-focus-1
```

Use `claude:SESSION-ID` for Claude. Include `--data-dir PATH` before `--activity` when using a nondefault VISOR data directory. An existing VISOR-managed agent credential also works through `--credential PATH` or `VISOR_AGENT_CREDENTIAL`. Credentials are private; never paste their token into a conversation.

New managed Codex and Claude sessions discover scoped MCP tools: `activity_list`, `activity_get`, `activity_publish`, `activity_control`, `activity_share`, `activity_asset`, `activity_replies`, `activity_acknowledge`, `activity_catalog`, `activity_template`, `activity_update_card` and `activity_events`. Already-running sessions can use the console commands immediately after access is enabled; they do not need hot MCP configuration support.

## Share a board

```sh
visor-console --activity share --session codex:OWNER-ID --id BOARD-ID --members codex:OWNER-ID claude:PEER-ID --request-id unique-share-1
visor-console --activity get BOARD-ID --session claude:PEER-ID
```

Sharing must be turned on. The board owner grants the required membership. Every member must already have enabled participation and an approved direct connection to every other member. Turning off a required connection blocks shared access. Private boards remain usable by their owner. Operator access remains available in VISOR.

Each session can add cards and update its own cards. Preserve other contributors' cards exactly when publishing the next document revision. Ownership and contributors are derived by the broker, not trusted from document labels. Read the latest board, increment `revision`, and send its previous revision as `--expected-revision`. Concurrent changes reject with a revision conflict; read and merge before a new attempt. Reuse the exact request ID and payload when retrying an uncertain result.

Operator typing and drawing are marked as operator edits. A stale edit is rejected while the draft remains visible. Compare the latest content and retain any text you need before **Reload card**, which discards that local draft. Layout and drafts persist separately from shared content.

## Content format

Every document has `schema_version: 1`, a stable `id`, `title`, positive `revision` and `cards` array. Optional `parent_record` links the topic to an owning record. Each card has a unique stable `id`, `title`, `kind` and optional `geometry: [x,y,width,height]` and `caption`.

| Kind | Content |
| --- | --- |
| `text` | `html`: useful typography and tables; scripts, links and external resources are removed from rendering |
| `image` | `path`: relative PNG/JPEG/WebP/GIF/BMP inside the document directory; publishing uploads it into private broker storage |
| `diagram` | `nodes: [{id,label,x,y,w,h,color}]`, `edges: [{from,to,label}]` |
| `chart` | `labels: [text]`, `values: [number]`, optional `unit` |
| `question` | `prompt`, optional distinct `choices` |
| `sketch` | `canvas: [width,height]`, `strokes: [{points:[[x,y],...],color:"#087d80",width:3}]` |
| `table` | `columns`, scalar `rows`, optional stable `row_ids` and bounded `table_style` |
| `gauge`, `meter`, `progress` | A numeric value/range, unit and optional detail |
| `indicator`, `status`, `counter`, `stat`, `sparkline`, `timer`, `arrow` | Portable status and summary displays; discover exact fields with `activity_catalog` |
| `action`, `toggle`, `slider`, `select` | Declared typed operator input; the contributor receives an intent event |

See `examples/activity/`. Run `python scripts/make_activity_demo.py /path/to/new-demo-folder` to generate all examples and the sample picture without downloading anything. The developer preview is `python scripts/activity_board.py --document /path/to/demo/shared-chalkboard.json --state-dir /path/to/private-preview-state`.

## Replies, recovery and limits

Saving an answer stores an event addressed to that question's contributor. The session reads it with `--activity replies --board-id BOARD-ID`, then acknowledges its exact event ID with `--activity ack EVENT-ID`. Acknowledgment means read; it never grants action approval or marks work complete. Replies are private to their recipient.

Board revisions and raster assets are canonical SQLite records and survive the existing VISOR state backup/restore route. Window projections can be rebuilt. Publish and edit receipts distinguish durable acceptance from display projection status. An accepted revision with `projection.state: pending` is saved canonically but is not confirmed displayed; retry the exact request or explicitly read/open the board to repair the projection. An unchanged inventory poll reads metadata without rehashing assets or rewriting files. Explicit reads and opens revalidate files and assets. Restoration clears old credentials, so enable session access again. Local layout and unsent drafts are separate local files; they are not included in a SQLite-only backup.

Operator-owned `activity_policy` controls document/image byte limits, pixel limits, card/text/answer/item counts and drawing points. The same validated policy applies at the broker boundary and in already-open windows. Lowering a limit preserves complete drafts and drawings while holding operations that exceed the new limit. Raising the limit can make those operations available again. Invalid policy places content operations on hold; closing, navigation and local draft saving remain available. Published content cannot execute HTML scripts or load remote images. Native rendering uses Qt; responsive web rendering uses the operator’s browser with a separate private view token.

This is a local feature build. Public releases and native Windows/macOS acceptance are separate delivery steps.

See the [Prism defect ledger](docs/PRISM_DEFECTS.md) for corrected failures and their regression tests.

## Crisp tables and contextual help

Use the `table` component for portable styling. Arbitrary HTML/CSS styling is not
the table authoring interface. `activity_catalog(kind="table")` supplies the
machine-readable schema, limits, styled examples and `clean`, `project`,
`metrics`, `risk` presets. Copy a preset into `table_style`, then adjust useful
details. [Styled project example](examples/prism/styled-project.json).

`table_style` accepts density, striping, grid visibility, a border, header style,
column/row/cell overrides and optional sorting/filtering/copying. Border colors
use `#RRGGBB`, widths are 0–8 logical pixels and styles are solid/dashed/dotted/none.
Cells can set background/text color, 10–28px text, bold, alignment, wrapping,
0–16px padding, a status badge and a plain tooltip. Columns can request 80–800px
widths. Unspecified colors inherit the viewer's theme.

Body styles merge in this order: table border, column, row, cell. Header styles
merge table border, header, column. Border properties merge independently.
Style indexes always reference source rows/columns, independent of local sorting
and filtering. Explicit borders take precedence over grid visibility.

Stable `row_ids` retain row selection when an agent changes values or reorders
rows. Without IDs, clients identify equal row content plus duplicate occurrence;
changing values can clear that fallback selection. Copy uses only selected
visible rows as TSV, preserving literal cell text. Native Ctrl/Cmd+C and browser
copy controls leave text editors' normal copy behavior intact. Header sizing,
scroll, filters and sorting survive ordinary data refreshes.

Any card can include plain `help` text (up to 1,000 characters) for its **?**
button. Optional `data_status` has a state (`ready`, `loading`, `empty`, `error`,
`stale`), message, source and timezone-aware `observed_at`. Ready stays quiet;
non-ready states appear as text cues. Source/data observation time is optional
detail, separate from the broker's revision timestamp. These are agent-supplied
claims; the UI does not independently verify the data source or freshness.

Small native cards scroll their full content so graphics, direct controls,
status and help remain reachable without overlapping. Web tables keep their
headers visible within their own scrolling area and reflow on mobile.

See the [agent workflow](docs/PRISM_AGENTS.md) for structured discovery,
read-only `activity_validate` preflight and precise update/retry behavior.

## Agent component catalog, templates and updates

```sh
visor-console --activity catalog --session codex:SESSION-ID
visor-console --activity template project-overview --id release --title "Release review" --session codex:SESSION-ID
visor-console --activity update-card release gauge-1 --changes '{"value":95,"detail":"19 of 20 checks passed"}' --expected-revision 1 --request-id update-2 --session codex:SESSION-ID
visor-console --activity events --board-id release --session codex:SESSION-ID
visor-console --activity ack EVENT-ID --session codex:SESSION-ID
```

`template` returns a complete board without publishing it. Customize and publish
using the existing command. Recipes include project-overview, status-board,
decision-board and an empty freeform board. The catalog describes fields and
examples for `gauge`, `meter`, `progress`, `indicator`, `status`, `counter`, `stat`,
`sparkline`, `timer`, `table`, `arrow`, `action`, `toggle`, `slider` and `select`.
See [synthetic project example](examples/prism/customer-portal.json).

Use one stable board/card ID and update changing values. `activity_update_card`
merges bounded property changes against an exact revision and uses the same
contributor permissions, optimistic concurrency and request receipts as publish.
A stale revision requires reading and merging. Retry the exact request ID and
payload after an uncertain result. A display update does not change an operator's
unsent draft or reopen a dismissed board.

A card may declare `"actions":[{"id":"view-checks","label":"View checks"}]`.
Interactive controls also declare an `action_id` and typed value; consult the
catalog for their exact shape. Controls generate recipient-scoped events with the
board/card/revision and an action ID. They do not execute commands or approve
work. Read `activity_events`, perform only otherwise authorized work, update the
board with the result, and acknowledge the exact event ID when read. Do not use
read acknowledgment to mean complete.

## Optional VISOR + Prism browser, tablet and phone

Web access is off by default. Choose **Web access…** from VISOR for explicit
Start/Stop, local/LAN/VPN binding and private Sessions/Prism links. The Sessions
view includes status, recent messages, an operator composer, and exact receipts
and final replies. Choose **Board actions → Open in browser** for Prism on the current server,
starting loopback only when no web server is running.
For an operator-managed network route:

```sh
visor-console --web --web-host YOUR-LAN-OR-VPN-IP --web-port 8765
```

Loopback is the default. Remote hosts are explicit IP addresses. Treat the
private share URL as access to VISOR sessions/messages and Prism; protect remote access with a
trusted network and HTTPS. No broker token is sent to the browser. Narrow screens
use readable stacked cards; larger screens can use the freeform canvas.
See [design, controls and platform references](docs/PRISM_DESIGN.md).
