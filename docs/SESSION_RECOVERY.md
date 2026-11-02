# Recovering closed terminal sessions

VISOR and the shared provider service can remain available after terminal windows
close. A saved conversation, an open terminal and running background work are
separate observations.

These steps use the desktop supervisor. The browser interface is unchanged.

1. Open VISOR and use **Refresh**. **Show all** restores sessions hidden by filters.
2. Select the sessions you need. Use Ctrl/Cmd-click to choose several rows.
3. Choose **Resume selected…** and confirm once that those saved sessions are not
   already running in other terminals.
4. Review each result. **Launch requested** means the normal Resume action was
   accepted; confirm the new window opens. **Skipped** explains an already loaded,
   linked, changed or uncertain session. **Error** preserves the reported failure;
   inspect the session before trying again because a launch outcome can be unclear.

The action refreshes provider metadata before choosing eligible saved sessions.
Sessions with a provider discovery warning are skipped; healthy providers continue.
Each launch retains the existing helper, terminal and provider ownership checks.
Selection uses exact session IDs, including when the table refreshes or sorts.
Individual launch failures are reported while the remaining selections continue.

**Saved** means the provider reports a disconnected or unloaded conversation and
no current VISOR terminal is reporting. It does not establish whether detached
background work has stopped. **Not attached** means a terminal is open while its
provider session has not attached yet. Other provider states remain visible.

Use **Focus** for a current terminal. Inspect a linked external window or use the
existing **Reconcile closed terminal…** action when old ownership needs checking.
The selected-session action conservatively skips loaded provider sessions; the
individual Resume action retains its existing behavior for deliberate reattachment.

Resuming opens the saved conversation. It does not replay failed or uncertain
requests, restart background jobs, or change communication pairs, browser tokens,
or settings. Review job checkpoints and any outstanding delivery receipts within
the recovered session before continuing work.
Use the owning session’s agent controls to resume its spawned workers.

This terminal workflow preserves the current VISOR database. Restoring a database
backup uses the separate [state recovery procedure](STATE_RECOVERY.md).

For saved dashboard visibility, choose **Prism** to reopen its workspace. If cards
are hidden, the workspace's existing **Actions → Show all cards** control restores
them explicitly. Terminal recovery leaves saved board visibility preferences alone.

The recovery UI is covered by isolated Qt tests and synthetic rendered previews.
Actual desktop acceptance remains platform-specific; retained Windows/macOS and
provider-history release limits still apply.
