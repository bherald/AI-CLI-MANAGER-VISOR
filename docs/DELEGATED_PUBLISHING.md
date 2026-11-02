# Scoped publication without transferring operator input

A contributor may let an existing permitted board member publish updates to an
exact set of its cards. The original card contributor, board owner, stable IDs,
membership, content history and operator reply/event recipients remain intact.
The publisher appears as `last_editor`; it does not become the card contributor
or receive operator commands. No card removal/recreation or secret copying is
needed. This is publication permission, not task/domain execution approval.

The normal authenticated broker and session-scoped CLI/MCP expose
`activity_publisher_get`, `activity_publisher_grant` and
`activity_publisher_revoke`. Only the contributor or authenticated operator can
change its delegation. The target must already be an existing permitted member
under the board's current sharing, participation and direct-pair policy. A board
owner cannot delegate another contributor's cards using an agent credential.

For the contributor's existing own CLI credential, substitute actual IDs:

```sh
visor-console --activity --session "$CONTRIBUTOR" publisher-get \
  --id "$BOARD" --contributor "$CONTRIBUTOR" --publisher "$PUBLISHER"
visor-console --activity --session "$CONTRIBUTOR" publisher-grant \
  --id "$BOARD" --contributor "$CONTRIBUTOR" --publisher "$PUBLISHER" \
  --card-ids "$CARD_ONE" "$CARD_TWO" \
  --expected-revision "$BOARD_REVISION" --expected-grant-revision 0 \
  --request-id "$GRANT_REQUEST_ID"
```

Read first: `expected_revision` is the current board revision, and
`expected_grant_revision` is the returned grant revision (zero for no grant).
Grant/revoke increments only that grant revision; it does not publish a new board
revision or change its presentation. Existing request-ID receipts preserve the
exact original request and reject changed retries. Revocation uses the same
identities and exact current revisions, without `--card-ids`.

The publisher uses its **own** credential, the current board revision and the
explicit `publisher_grants` map `{ "source-contributor-id": grant_revision }`
with normal `activity_validate`, `activity_publish` or `activity_update_card`.
CLI equivalents accept `--publisher-grants` as that JSON object. New calls refuse
revoked or changed grant revisions. Historical idempotent receipts remain
readable; retrieving one does not create another publication.

Delegated publication preserves all existing card IDs/order and board metadata;
it cannot add/remove cards, transfer contribution or modify ungranted content.
The normal schema/assets/policy/revision guards remain, and grant permissions
are rechecked in the actual publication transaction. Operator input continues
to use the unchanged contributor even after a publisher update. An activity-only
publisher credential is not an operator or message-delivery credential.

The additive table uses existing private activity storage, transactions,
request receipts and backup-table discovery. Source tests use isolated synthetic
profiles and identities; they do not grant a live delegation. The integration
owner reviews/adopts source before invoking the normal grant route. Revoke the
selected grant and stop its caller before rolling source back; retain board,
grant and request history and reconcile uncertain publication without replay.
