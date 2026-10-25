# Prism defect ledger

These fixes are included in the local 1.4.0 beta candidate. The entries below describe the defect, correction and regression coverage. Focused checks passed during development; final combined source/build verification is recorded separately in the release evidence. A passing source test does not establish native desktop acceptance on every platform.

## PRISM-1 — A committed edit could be reported as failed

- **Trigger:** Publishing or editing commits a revision, then writing its display projection fails.
- **Cause:** Projection exceptions escaped after the canonical transaction committed. The GUI could also replace accepted text or drawing with an older projection.
- **Fix:** Durable acceptance and request identity are separate from projection status. Exact retries recover the same receipt. Accepted content remains visible and recoverable until the current projection arrives; a pending open does not use a cached path or claim presentation.
- **Regression coverage:** `tests/test_activity_projection.py` covers postcommit failure, lost-response/restart retries and precommit rejection. `tests/test_activity_accepted_ui.py` covers accepted text/sketch recovery, newer drafts and explicit open repair.

## PRISM-2 — Inventory polling repeated expensive projection work

- **Trigger:** Unchanged board inventories are polled while other sessions publish, reply or delete.
- **Cause:** Repeated projection validation, asset hashing and file work could unnecessarily acquire SQLite writer transactions and delay unrelated activity.
- **Fix:** Unchanged inventories use metadata and a projection cache keyed by revision and validated policy. Expensive preparation occurs outside writer transactions; short commit checks prevent stale writes or deleted-board resurrection. Explicit reads and opens validate and repair projections.
- **Regression coverage:** `tests/test_activity_projection.py` covers unchanged polls, concurrent slow projections, stale staging, cache invalidation, explicit repair and asset-validation ordering. `tests/test_activity_projection_order.py` verifies that a late failed repair cannot replace a newer successful repair at the same revision.
- **Limit:** Unchanged polling intentionally does not rescan files. Explicit reads/opens remain the integrity-validation route.

## PRISM-3 — Open windows kept stale limits and could lose saved drafts

- **Trigger:** An operator changes limits while a board is open, or reopens drafts after lowering limits.
- **Cause:** Widgets retained their construction-time policy. Draft restoration truncated answers and rejected drawings against the newer limits.
- **Fix:** Complete validated policy changes reach existing windows and widgets. Operations recheck current limits; invalid policy puts content operations on hold. Complete answers, text, drawings and retry identities are retained. Over-limit drafts remain available but cannot be sent until valid. Navigation and local draft/layout saving remain available.
- **Regression coverage:** `tests/test_activity_live_policy_ui.py` compares live answer, image-byte/pixel, drawing-point and stroke-count limits with the broker, including invalid-policy holds and complete draft restoration. `tests/test_activity_sketch.py` covers bounded pointer input.

## PRISM-4 — Small question cards overlapped controls and read status

- **Trigger:** A restored small card contains a long prompt, answer or recipient acknowledgment.
- **Cause:** The question form was compressed into the subwindow without sufficient scroll containment.
- **Fix:** Question contents use a resizable scroll area with layout-derived minimum content size. Prompts, answers, buttons and full status text remain reachable without shortening their contents.
- **Regression coverage:** `tests/test_activity_live_policy_ui.py::test_restored_small_question_card_scrolls_prompt_reply_controls_and_full_read_status` checks restored 300×180 geometry, nonoverlapping controls, scrolling, draft preservation and the full read label. Synthetic Qt renderings were inspected.
- **Limit:** Native interactive Windows/macOS, mixed-DPI, screen-reader and broader accessibility acceptance remain separate checks.

## PRISM-5 — Retired window destruction can run on a worker thread

- **Trigger/root cause:** A retired Prism window loses manager ownership but retains Python reference cycles. Garbage collection on a worker can then destroy its Qt widgets on that worker, violating GUI thread ownership. A full mixed-suite crash exposed the risk; the isolated forced-GC probe independently demonstrates the invalid destruction thread.
- **Fix:** Schedule retired windows for Qt deferred deletion on their owning GUI thread after disabling callbacks and timers. Drain deferred deletion in affected UI test fixtures. Ordinary operator close/reopen behavior is unchanged.
- **Regression:** `tests/test_activity_qt_lifetime.py` forces worker GC before GUI event processing, verifies destruction on the GUI thread, then delivers a late reply callback after deletion and verifies no draft recreation. The regression fails on the prior source. The focused mixed UI/projection suite passes 54 tests; lint passes.
- **Verification limit:** The exact original segmentation fault was not reproduced in smaller test slices. Full frozen-suite and native package validation must be repeated after integration; this evidence proves the ownership defect and its fix, not exclusive causation of that crash.
