# Claude Code plugin distribution

`visor/` is the plugin payload for an operator- or organization-maintained Claude plugin
marketplace. It supplies the same MCP channel and tools as VISOR's local preview mode.

For local development, select **Claude Code settings → Local preview channel** in VISOR.
This uses the documented `--dangerously-load-development-channels server:visor` flag.
Claude presents its own confirmation; organization restrictions and authentication still
apply. No tool permissions are skipped and no permission relay capability is declared.

For an approved distribution, put this plugin in your marketplace, install it through
Claude's normal plugin flow, and obtain the required channel allowlisting. Then select
**Approved plugin** in VISOR and enter `plugin:visor@your-marketplace`. VISOR uses
`--channels`, without the development flag. The payload is not currently on Anthropic's
public allowlist; shipping these files does not grant approval.

Launch through the packaged VISOR application. Its terminal helper sets `VISOR_EXECUTABLE`
to the packaged console helper and `VISOR_AGENT_CREDENTIAL` to that session's credential
file. The plugin expands those environment variables in `.mcp.json`. The credential is
session-scoped and cannot call operator or helper controls. Development from Python source
can use inbox or local preview mode; the approved plugin payload expects the packaged helper.

The channel writes `notifications/claude/channel`. Claude does not acknowledge these
notifications at the protocol level and may silently discard them when channel policy
blocks them. VISOR therefore records delivery-unknown until the recipient calls its
acknowledge tool. Neither restart nor an uncertain write automatically retries the message.

Live Claude model testing is waived by the operator. Local protocol/subprocess tests cover
the actual broker, channel framing, scoped hooks, policy, cross-provider routing, explicit
completion and restart reconnection with a simulated client.

Sources: [CLI](https://code.claude.com/docs/en/cli-reference),
[channels](https://code.claude.com/docs/en/channels),
[channel protocol](https://code.claude.com/docs/en/channels-reference),
[hooks](https://code.claude.com/docs/en/hooks),
[agent view](https://code.claude.com/docs/en/agent-view).
