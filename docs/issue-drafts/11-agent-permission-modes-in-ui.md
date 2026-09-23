## Request

Let users pick the AI agent's autonomy level from the chat UI, the way Claude
Code does, instead of it being a server-side config value. Four modes:

| Mode | Behaviour |
| :--- | :--- |
| **Manual** | Ask for approval before every edit. |
| **Edit automatically** | Edit the selected text or the whole file without asking. |
| **Plan** | Explore and present a plan before editing anything. |
| **Auto** | Approve actions that pass a safety check, pause for anything risky. |

With a keyboard shortcut to cycle them mid-session.

## Why

During an incident the right level of autonomy changes minute to minute. Reading
logs and correlating alerts wants Auto; touching anything in a live cluster wants
Manual; working out what to do at all wants Plan. Today that choice is made once,
at deploy time, by whoever set the config - and it applies to every user and
every session until someone redeploys.

The practical result is that the setting gets pinned to whichever extreme hurts
least, and the agent is either too slow to be useful or too free to be trusted.

## Current state

Most of the machinery is already there. `session/config.py:51` carries a
`permission_mode` field, and `config/settings.py:96` reads it from
`AI_AGENT_PERMISSION_MODE` or `config.yaml`, defaulting to `default`. It is
passed straight into the SDK options at `session/config.py:168`.

What is missing is that it is **process-wide and fixed**. There is no way for a
user to see it, choose it, or change it for one session. `ws_chat.py:81` already
sends both `permission_mode` and `require_tool_approval` to the client, so the
client knows the value - it just cannot set it.

The one existing escape hatch is a blunt one: `require_tool_approval: false`
collapses to `bypassPermissions` (`session/config.py:155`), which is all-or-
nothing and currently fatal as root - see the separate `bypassPermissions` draft.

So the SDK-side vocabulary already matches the four modes requested:
`default` (Manual), `acceptEdits` (Edit automatically), `plan` (Plan), and a
safety-checked variant for Auto.

## Proposal

1. **Per-session mode, not per-process.** Move `permission_mode` from a config
   value to session state, with the configured value as the default for new
   sessions. The existing env var and `config.yaml` key stay as the default and
   as an administrative ceiling.
2. **A WebSocket message to change it** mid-session, so the mode can be switched
   without dropping the conversation. The session already round-trips state over
   `ws_chat.py`; this is one more message type.
3. **A mode picker in the chat UI**, showing the active mode and a shortcut to
   cycle. Mirrors what users already know from Claude Code.
4. **Auto needs a definition.** The other three map onto SDK modes directly. Auto
   is "approve what passes a safety check" and needs an explicit, reviewable
   policy: which tools auto-approve, and what counts as risky. Probably read-only
   tools auto-approve and anything that writes, deletes, or touches the cluster
   pauses. This is the part that needs design, not just plumbing.
5. **An administrative ceiling.** An install should be able to say "no session may
   exceed Manual" regardless of what a user picks, so the UI cannot be used to
   escalate past what the deployment allows.

## Open questions

- Should the chosen mode persist across sessions per user, or reset to the
  configured default each time? Resetting is safer; persisting is what people
  will expect.
- Does the mode belong in the audit log? Changing autonomy level mid-incident is
  exactly the kind of thing worth being able to reconstruct afterwards.
- Should Plan mode be able to hand its plan to a subsequent Auto run, or does
  every mode change start clean?

## Test

Needs coverage that a session created with the default mode can be switched at
runtime and that the new mode reaches the SDK options; that an administrative
ceiling is enforced against a client asking for a broader mode; and that Auto
pauses on a tool classified as risky.

## Related

The `bypassPermissions is fatal as root` draft. That bug is the current
consequence of having only one coarse toggle, and this feature is what replaces
the toggle.
