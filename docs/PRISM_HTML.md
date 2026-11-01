# Prism text-card links and formatting

Text cards support readable static tables and HTTP(S) links in both the native
window and browser view. Links open only when the operator activates them by
mouse or keyboard. They open externally; rendering or refreshing a card does not
navigate, execute a command, or mark work complete.

The native and web server use the same sanitizer. The browser revalidates the
result while copying it into its live DOM. Scripts, event handlers, embedded
resources, forms, frames, SVG/MathML, file/data/javascript URLs, relative URLs,
URL userinfo, recognized credential query/fragment parameters, and ambiguous
control/backslash URLs are rejected. Credential names include token variants,
password/passwd/pass, secret/client_secret, API/access keys, authorization/auth,
session_id, and signing credentials/signatures. Names are case-insensitive;
encoded names and punctuation variants are checked. Ordinary query parameters
(such as `item`, `q`, and `page`) and section fragments remain unchanged. Arbitrary
opaque values cannot be classified as secrets: publishers must keep credentials
out of card content, including unnamed fragments and nested URLs.
Native text documents retain a resource loader that never reads local or remote
resources. Browser links use a separate target with no opener or referrer.

Useful supported formatting includes:

- Existing paragraphs, headings, emphasis, lists, code blocks, and tables.
- Hexadecimal `#RRGGBB` foreground/background colors.
- Table borders from 0–8px using solid, dashed, dotted, or none, following the
  existing table validator. Legacy `bordercolor` becomes validated CSS.
- Table/cell widths of 1–100% or 80–800px; padding/spacing/margins from 0–16px.
- Column/row spans from 1–100, alignment, bounded font styling, and 1–28px/pt text
  (small authored spacer cells remain supported).

Unsupported CSS is omitted rather than interpreted. Resource-bearing styles
such as `background-image` and `url(...)` are never accepted. Authored foreground
and background colors are preserved: choose a readable pair, including text
outside table cells, for the selected Prism theme.

For sorting, filtering, selection, copying, and stable source-row identities,
continue using the existing `table` component and its catalog. This HTML change
adds no command/action permissions and does not change decisions, revisions,
drafts, custody, or shared board state.

The regression checks exercise actual native mouse and keyboard activation with
a mocked external opener, native document table formatting, browser DOM/rendered
styles, and malicious markup. Merely finding a URL in saved HTML is not link
acceptance. Private publisher fixtures and session URLs must stay outside public
source tests and screenshots; the repository fixture uses synthetic names and
`example.test` destinations.

Delivery: package and verify the reviewed source before an owner-controlled
upgrade. Keep the prior package for rollback; this correction changes rendering,
not stored board content or the database schema. No installer or live service is
changed by a source test.

References: [Qt link activation](https://doc.qt.io/qt-6/qtextbrowser.html#anchorClicked)
and [Qt's supported HTML/CSS subset](https://doc.qt.io/qt-6/richtext-html-subset.html).
