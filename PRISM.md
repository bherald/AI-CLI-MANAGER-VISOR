# Prism windows

Prism is a shared chalkboard beside your Codex and Claude conversations. A window holds movable, resizable cards: formatted text, pictures, diagrams, charts, questions and freehand drawings. Several windows can stay open at once.

In VISOR, choose **Prism** to reopen a board. Drag card title bars, resize their corners, type into a text card or draw on a sketch. **Save changes** shares your edit. **Clear view** and closing a window preserve the board, drafts and session work. **Export picture** saves the visible workspace as PNG.

## Close, reopen or delete a saved board

Closing a Prism window records your dismissal. Content can continue to update quietly, including after restarting VISOR. A session can request reopening; that request appears in the Prism chooser without reopening the window. Choose **Prism** to reopen it yourself. An authenticated operator control request can also reopen it. Showing a window is not proof that you read its content.

Agents can inspect `view_state` from `activity_list` or `activity_get`: dismissal, presentation, the last presented revision, unshown updates and a pending reopen request. Keep the same board ID; do not create replacement boards to bypass an operator dismissal. Repeated reopen requests coalesce into one indicator.

From the main menu, choose **Manage Prism windows…**, select a saved board and choose **Delete selected…**. Nothing is selected automatically. Confirm the named board and revision to delete its content, history, replies and membership, close its view and remove its local layout and drafts. Shared image assets and existing backups are retained. Codex and Claude sessions and conversations continue unchanged. This operator-only deletion is separate from closing a window; deleted board IDs cannot be reused by stale session requests.

If the deletion result is uncertain, **Retry same deletion** reuses the saved request. If the board changed, refresh and review the new revision before confirming again.

## Give a running session access

Choose **Enable Prism access** from that session's menu. This creates a private, session-scoped Prism credential. It does not restart the session, change global provider settings, or grant supervisor controls. Use the console executable from this VISOR installation:

```sh
visor-console --activity list --session codex:SESSION-ID
visor-console --activity publish --session codex:SESSION-ID --document board.json --request-id unique-publish-1
visor-console --activity focus BOARD-ID --session codex:SESSION-ID --request-id unique-focus-1
```

Use `claude:SESSION-ID` for Claude. Include `--data-dir PATH` before `--activity` when using a nondefault VISOR data directory. An existing VISOR-managed agent credential also works through `--credential PATH` or `VISOR_AGENT_CREDENTIAL`. Credentials are private; never paste their token into a conversation.

New managed Codex and Claude sessions discover eight additional MCP tools: `activity_list`, `activity_get`, `activity_publish`, `activity_control`, `activity_share`, `activity_asset`, `activity_replies` and `activity_acknowledge`. Already-running sessions can use the console commands immediately after access is enabled; they do not need hot MCP configuration support.

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

See `examples/activity/`. Run `python scripts/make_activity_demo.py /path/to/new-demo-folder` to generate all examples and the sample picture without downloading anything. The developer preview is `python scripts/activity_board.py --document /path/to/demo/shared-chalkboard.json --state-dir /path/to/private-preview-state`.

## Replies, recovery and limits

Saving an answer stores an event addressed to that question's contributor. The session reads it with `--activity replies --board-id BOARD-ID`, then acknowledges its exact event ID with `--activity ack EVENT-ID`. Acknowledgment means read; it never grants action approval or marks work complete. Replies are private to their recipient.

Board revisions and raster assets are canonical SQLite records and survive the existing VISOR state backup/restore route. Window projections can be rebuilt. Publish and edit receipts distinguish durable acceptance from display projection status. An accepted revision with `projection.state: pending` is saved canonically but is not confirmed displayed; retry the exact request or explicitly read/open the board to repair the projection. An unchanged inventory poll reads metadata without rehashing assets or rewriting files. Explicit reads and opens revalidate files and assets. Restoration clears old credentials, so enable session access again. Local layout and unsent drafts are separate local files; they are not included in a SQLite-only backup.

Operator-owned `activity_policy` controls document/image byte limits, pixel limits, card/text/answer/item counts and drawing points. The same validated policy applies at the broker boundary and in already-open windows. Lowering a limit preserves complete drafts and drawings while holding operations that exceed the new limit. Raising the limit can make those operations available again. Invalid policy places content operations on hold; closing, navigation and local draft saving remain available. No arbitrary HTML execution, remote image loading, browser engine or new dependency is used.

This is a local feature build. Public releases and native Windows/macOS acceptance are separate delivery steps.

See the [Prism defect ledger](docs/PRISM_DEFECTS.md) for corrected failures and their regression tests.
