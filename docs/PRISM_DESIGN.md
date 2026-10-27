# Prism workspace and portable components

VISOR 1.5.0 keeps independent boards in one resizable native Prism window.
Wide previous/next buttons and the board picker switch boards without discarding
their layouts or drafts. Connected sessions use readable names. Board and card
actions start closed; question answers and declared direct controls stay usable
in the card body. Controls occupy layout rows outside gadget painting areas.

VISOR supplies reusable display/input capabilities. Consuming applications choose
their workflow, conventions and best practices through those interfaces.

## Interaction vocabulary

| Term | Meaning |
| --- | --- |
| Session | An independent provider conversation |
| Connection | An explicitly permitted pair of sessions |
| Board | A shared Prism document, with independent cards and revisions |
| Hide / Show | Change presentation; preserve documents, sessions and drafts |
| Delete | Permanently remove the selected records after confirmation |
| Send reply / Send value | Record operator input for the contributing session |
| Read | The recipient acknowledged reading input; does not establish completion |

Supervisor tables support click selection, Ctrl/Cmd click toggling, Shift range
selection and Ctrl/Cmd+A for visible rows. Bulk actions use the selected records,
with guarded deletion and partial-failure reporting. Filtering never silently
adds hidden records to a selection. Keyboard deletion does not apply while
editing text.

## Board composition

Agents can compose freeform boards from ordinary schema-version-1 JSON. Optional
templates provide a starting point, without constraining the layout. Every card
has a stable ID, title, kind and optional `[x,y,width,height]` geometry. Native
cards can move, resize, overlap, move forward/backward, tile and snap to a grid;
layout undo/redo does not undo shared content or erase drafts. Diagrams describe
explicit node/edge connections; arrow cards can be placed independently.

The catalog exposes gauges, meters, progress, indicators, status, counters,
statistics, sparklines, timers, tables, arrows, buttons, toggles, sliders and
selectors, alongside safe rich text, raster images, charts, diagrams, sketches
and questions. Emoji use the system font. PNG/JPG assets use the existing bounded
upload and contained local-asset path rules. Published HTML cannot execute
scripts, navigate links or load remote resources.

Every component may declare up to eight `{id,label}` direct actions. Interactive
controls declare an `action_id` and emit a typed scalar value. The broker checks
the current board revision, declared action, value type, access and recipient.
An event records intent; it does not execute a shell command, grant permission
or mark a task complete. Agents read their events and acknowledge the exact
event ID. Retry IDs prevent duplicate input after an uncertain receipt.

Styled tables use the same bounded formatting contract on both renderers: border color/width/style, header/column/row/cell overrides, badges, help and optional sort/filter/copy. Stable row IDs retain selection on live updates. Small native cards scroll instead of forcing graphics and controls into overlapping space.

Agent discovery exposes machine-readable schemas, effective limits, optional presets and read-only preflight. The runtime validator remains authoritative for cross-field rules. [Agent workflow](PRISM_AGENTS.md).

## Desktop, browser, tablet and phone

The native Qt workspace serves desktop Linux, Windows and Mac. The optional combined
VISOR and Prism browser view provides phone/tablet/web access: a readable vertical layout on
narrow screens and a canvas on wider screens. It uses the same documents and
typed actions. A separate native iOS/Android app is not part of this release.

The server is off by default. **Web access…** in VISOR explicitly starts/stops
it, shows its private link, and chooses local-only or a specific LAN/VPN address.
The Sessions view includes readable session status, recent messages, an operator
composer and exact read/completion/final-reply receipts; Prism is a sibling view.
Browser actions use fixed existing APIs. This version provides the prototype’s
supervisory/message scope, while terminal lifecycle/admin remains in the desktop.

**Open in browser** opens Prism on the current server; it starts loopback only
when no server is running. CLI `--web` (`--prism-web` alias) supports an
explicit bind IP/port for an operator-managed LAN/VPN route. The browser receives
a separate private view token, not the VISOR broker credential. A share URL
grants access to VISOR sessions/messages and Prism boards: share it only with intended operators.
Remote deployment needs a protected network and HTTPS supplied by the operator;
VISOR does not publish a public endpoint or configure a tunnel automatically.

The calm slate palette uses restrained depth and teal/amber/lavender accents.
Status uses text or symbols as well as color. Direct controls remain separate
from graphics during resize. Native calm-dark/soft-light themes and browser themes, reflow and reduced-motion preferences
support long viewing sessions. Native controls preserve the platform keyboard
conventions; complete assistive-technology/native-device acceptance is pending.

## Design references and acceptance scope

These are design references, not a claim that VISOR is certified or fully
conformant. WCAG 2.2 AA is the web accessibility target, with WCAG2ICT guidance
for native UI. Platform conventions refine keyboard, touch, focus and selection:

- [Microsoft Fluent 2 accessibility](https://fluent2.microsoft.design/accessibility)
  and [keyboard interactions](https://learn.microsoft.com/en-us/windows/apps/develop/input/keyboard-interactions).
- [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/),
  [WCAG2ICT](https://www.w3.org/TR/wcag2ict/) and
  [ARIA Authoring Practices](https://www.w3.org/WAI/ARIA/apg/).
- [KDE HIG](https://develop.kde.org/hig/) and [GNOME HIG](https://developer.gnome.org/hig/)
  for Linux desktop behavior.
- [Apple Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/)
  for Mac/iPhone/iPad conventions, and [Material 3](https://m3.material.io/)
  for Android/touch guidance.

Linux source/package checks, synthetic desktop/browser rendering and regression
tests cannot establish Windows/Mac native acceptance, real phone behavior or a
screen-reader audit. Release evidence records those boundaries explicitly.

Operational setup, lifetime, private links, network choices and troubleshooting are documented in [Web access](WEB_ACCESS.md). The table structure follows the [W3C tables guidance](https://www.w3.org/WAI/tutorials/tables/); native rendering uses standard [Qt item delegates](https://doc.qt.io/qt-6/qstyleditemdelegate.html). These references support implementation choices, not a conformance claim.
