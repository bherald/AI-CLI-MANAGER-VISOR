# Native messages and replies

The native supervisor saves an operator message's recipient, normalized outgoing
text and request ID before attempting Send. If the save response is lost, no send
is attempted. If the send response is lost or does not match those fields, the
composer stays pinned and **Retry same message** reuses that exact request. This
reconciles the existing enqueue receipt; it does not release or repeat an uncertain
transport delivery. The pending request survives closing and reopening VISOR.

Recipient and text stay locked while that request is unresolved. A malformed saved
pending request or conflicting local draft is held without overwriting either
record. A disconnected supervisor retains its draft and asks for reconnection.
An unloaded saved draft is never replaced by a blank draft during close.

Select one message and choose **Receipt / reply**, double-click it, or choose
**View receipt / reply** from its context menu. The inspector reads that exact
message through the existing operator-only `message_get` action. It shows the
full original text, delivery/read/completion information, and the final reply
bound to that message and recipient. **Refresh receipt** reads it again; opening
the inspector does not acknowledge, complete, release or send anything. Missing,
deleted, changed or unavailable records display an explicit lookup error.

**Compose follow-up** selects the original session for a new operator message.
It preserves an existing draft for another recipient and cannot change a pending
send. It never sends automatically. Reply, read acknowledgment and completion
remain separate facts; a reply alone does not prove completion.

**Needs attention** contains `held`, `failed` and `delivery_unknown`. Queued,
sending and ordinary waiting traffic remain under **All messages**. **Finished**
still contains completed and failed messages; its existing confirmation and
deletion rules are unchanged.

## Implementation and compatibility

The prior native composer generated a new request ID on every click, so retrying
after an accepted response was lost could create another message. Its message
viewer used only the bounded list snapshot, which contains no final reply, and
the attention filter meant every nonterminal state while excluding failed.

The existing `message_draft` setting accepts its legacy `{text, recipient}` shape
or that shape plus optional `pending: {request_id, recipient, body}`. Pending
requests require a canonical UUID v4, valid UTF-8 text of 1–16000 Unicode code
points after normalization, and exact agreement with the displayed draft and
recipient. Invalid updates fail before writing. No store schema, permission,
message state or background service is added. Native receipt verification checks
the operator sender/origin, message kind, ID, recipient, body and enqueue `dedupe`
request binding before clearing a pending draft.

The existing generic RPC limit remains in place. Only authenticated `send`
(operator/agent) and `message_draft` (operator) requests get a schema-derived
JSON byte budget large enough for escaped Unicode and the duplicate pending body.
The action header must match the actual action, and the same typed text limits
still apply. Helper/activity credentials gain no messaging or draft permissions.
The composer preserves Unicode code points when Qt counts UTF-16 units differently.

Focused tests use disposable Broker/Store instances and fault injection for lost
save/send responses, restart, serialized autosaves, bad receipts and stale reply
callbacks. Synthetic offscreen desktop/compact renders cover the composer,
attention filter and full reply inspector. Interactive Windows/Mac and packaged
runtime acceptance remain separate release checks.

## Rollback

Roll back the native UI and typed draft contract together. Before running an older
build, reconcile any pending send with the exact request ID and preserve its draft
record; an older UI does not understand pending custody and can overwrite it.
Do not clear an uncertain request to make rollback appear successful. No database
migration or model/provider change needs reversal.
