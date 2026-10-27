# Agent access and a reliable Prism authoring workflow

VISOR advertises its agent interface through **MCP**. New managed Codex/Claude
sessions receive a session-scoped MCP connection at launch. Initialization
instructions explain the workflow; tools/list supplies tool descriptions and
input schemas. Agents do not need a separate VISOR/Prism plugin.

Already-running clients may cache their MCP inventory. Use **Enable Prism access**
from the session menu and the current installed `visor-console --activity` CLI.
Do not restart a working provider session just to discover new Prism tools.
Scoped catalog responses include a trusted `local_cli.command` argv for the
current installation. Reuse the session credential; the response contains no
credential token. Operator/browser catalogs omit these private runtime paths.
The operator-issued credential scopes identity; never substitute an operator
token, copy credentials into a prompt or forge a contributor field.

## Ownership

VISOR owns the reusable renderer, optional web surface, bounded component schemas
and scoped MCP/CLI interfaces. Consuming applications own their domain workflows,
best practices, approvals and canonical records. Generic presets are optional; a
consumer may supply its own templates without rebuilding the UI implementation.

## Discover once, reuse the contract

`activity_catalog` lists the supported components, presets, effective limits,
contract revision and authoring sequence. Cache by `contract_revision` and limits.
Request `kind: "table"` for a focused machine-readable card schema and examples;
`include_schema: true` supplies the complete document schema. Cross-field rules
are annotated and checked by the actual runtime validator.

1. `activity_list` / `activity_get`: reuse a suitable board and read its current
   revision, sharing, presentation and operator dismissal.
2. `activity_catalog`: choose supported fields and a small, useful composition.
3. `activity_template`: optionally start with project-overview, status-board,
   decision-board or freeform. It returns JSON without publishing anything.
4. `activity_validate`: run read-only preflight. Repair its structured error
   `code`, JSON `path` and `message`; consider its recommendations.
5. `activity_publish`: use the exact expected revision and a fresh logical
   request ID. Retry uncertain results with the identical ID and payload.
6. `activity_update_card`: replace only properties that changed, using the current
   revision. This is a top-level property replacement, not a recursive merge.
7. `activity_events` / `activity_replies`: read operator input at safe boundaries;
   acknowledge the exact ID after reading. Perform only otherwise authorized work.

Preflight checks schema, current revision, contributor ownership and assets
without a database/projection write or a window-opening request. It does not
reserve a revision. Publish repeats the checks because state can change.
Uploaded image paths must already be broker-owned for broker asset preflight.

## Compose useful interfaces

Lead with outcome, current state and the next decision/action. Use a small number
of cards, friendly titles and concise labels. Choose a gadget only when it makes
the information easier to read. Preserve stable board/card IDs and update values;
do not create replacement boards to evade an operator dismissal.

Prefer the reusable table presets `clean`, `project`, `metrics` or `risk`; copy
the returned style object into `table_style`, then override only meaningful
details. Freeform JSON remains supported. Strong colors, thick borders and badges
should highlight decisions/exceptions. Include text as well as color. Use `help`
and cell `tooltip` for optional detail instead of crowding the card body.

Supply stable `row_ids` for changing tables. Styles address original row/column
positions, independent of the viewer's local sort/filter. Without IDs, clients
derive row-content fingerprints; changing a value then changes that fallback
identity, so selection can clear. Local sort/filter/copy never publishes changes.

Use optional `data_status` for `ready`, `loading`, `empty`, `error` or `stale`.
Its `source` and timezone-aware `observed_at` describe the agent-supplied data
observation. They are not independently verified telemetry or the broker's edit
timestamp. Keep last useful data when reporting a refresh failure, and describe
the failure concisely. Do not invent progress, freshness or verification.

## CLI examples

Use the console helper from the current installation:

```sh
visor-console --activity catalog --kind table --session codex:SESSION-ID
visor-console --activity template project-overview --id release --title "Release review" --session codex:SESSION-ID
visor-console --activity validate --document board.json --expected-revision 0 --session codex:SESSION-ID
visor-console --activity publish --document board.json --expected-revision 0 --request-id publish-1 --session codex:SESSION-ID
visor-console --activity update-card release table-2 --changes '{"rows":[["Final checks","Quality","Verified"]],"row_ids":["final-checks"]}' --expected-revision 1 --request-id update-2 --session codex:SESSION-ID
visor-console --activity events --board-id release --session codex:SESSION-ID
visor-console --activity ack EVENT-ID --session codex:SESSION-ID
```

Use `claude:SESSION-ID` for Claude and the `.exe` helper on Windows. Add
`--data-dir PATH` before `--activity` for a nondefault data directory. The CLI
shares the same broker validators, authorization and request receipts as MCP.

## Receipt and lifecycle rules

An accepted revision may still have a pending display projection. Inspect its
receipt and current `view_state`; publication is not proof of presentation or
reading. A dismissed board stays dismissed until operator reopening. A session
open/focus request becomes a quiet reopen indicator, not forced focus.

Direct actions/toggles/sliders/selectors emit declared, typed scalar intent to
their contributing session. They cannot embed commands or grant approval.
Reading, completion, approval and a final operator reply are distinct. VISOR
messages use `peers`, `send`, `receive`, `acknowledge` and `operator_reply`; normal
connection/recipient restrictions remain in force.

See [Prism fields](../PRISM.md), [web access](WEB_ACCESS.md),
[design references](PRISM_DESIGN.md) and [release evidence](RELEASE_1_5_0.md).
