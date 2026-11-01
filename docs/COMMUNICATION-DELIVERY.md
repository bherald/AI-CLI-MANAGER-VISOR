# Independent message delivery

VISOR previously visited Codex recipients in one serial loop and held its shared
delivery lock during each provider command. One slow command could delay every
other recipient and the operator's communication controls for its 30-second
timeout.

The existing durable outbox now admits independent recipients through a bounded
worker pool. A recipient has only one provider delivery in progress. Claims still
check the current session, direct-pair and participation policy. A pull from that
same inbox cannot overtake the admitted provider call. Unrelated recipients and
policy controls can proceed while the call waits.

Permanent deletion gates its exact target and discovered descendants before
waiting for their admitted calls. Other recipients and policy controls continue;
history is deleted only after the target's calls and verified closure finish.
Failure releases the temporary gate and preserves the existing deletion rules.

## Configuration and receipts

The operator-only `delivery_options` RPC reads or writes the existing SQLite
setting `{"max_parallel": 4}`. Accepted values are integers from 1 to 32; invalid
stored policy is reported rather than silently replaced. A changed setting takes
effect at the next normal broker restart. The snapshot's `delivery_parallelism`
reports the active limit, or null before the delivery worker starts. This limit
controls concurrent transport commands, not AI models or task execution.

Delivery reads the complete queued-recipient set, independently of the recent
200-message UI view. A provider error or uncertain send retains
`delivery_unknown`; it never queues a retry automatically. Acceptance is still
separate from reading and work completion. Shutdown stops admission and waits for
the bounded, already admitted provider calls. Claude's approved inbox/channel
behavior is unchanged.

## Validation and rollout

The isolated regression reproduces the original slow-recipient blockage, then
shows a healthy recipient being accepted before the slow fixture is released.
Additional cases cover configured capacity, per-recipient order, MCP pull,
UNKNOWN preservation, policy changes, deletion and queues beyond the UI window.
An independent combined countercase also keeps healthy delivery and policy changes
working while a deletion waits for its slow target. These are synthetic provider
tests, not a measured live throughput improvement.

Install the matched supervisor/broker candidate through the normal local upgrade
route after its checks. Preserve private state, active requests and verified
process custody; do not force-stop or resend uncertain work. Confirm the reported
build and active delivery limit after restart. Rollback uses the retained matched
package and private-state recovery procedure; outstanding UNKNOWN requests stay
held for reconciliation. See [native messages](NATIVE_MESSAGES.md) for the new
draft's separate preservation requirements.
