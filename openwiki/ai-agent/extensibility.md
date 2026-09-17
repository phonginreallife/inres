---
type: subsystem
title: "Agent Extensibility: MCP, Plugins and Memory"
description: How the InRes AI agent gains capabilities beyond its built-in incident tools — per-user MCP servers drawn from Postgres, git-cloned marketplaces, synced skills and CLAUDE.md memory — and how each user's workspace is kept isolated.
tags: [ai-agent, mcp, plugins, marketplace, extensibility, workspace, memory, skills]
verified:
  - by: openwiki/0.4.3
    at: 2026-09-17T10:09:55.322Z
sources:
  - id: openwiki-source-96f1ddb9d1ea4d764cbe2b8b
    resource: repo://server/agent/routes/marketplace.py
  - id: openwiki-source-d9651fb274eb0ee2e7cf6498
    resource: repo://server/agent/services/storage.py
  - id: openwiki-source-9ffbf56345282cc929bd3977
    resource: repo://server/agent/session/config.py
  - id: openwiki-source-8c63e785dee7eaab836ff6a5
    resource: repo://server/agent/streaming/mcp_client.py
  - id: openwiki-source-ec95db14dcbdf8225cbd19ea
    resource: repo://server/agent/streaming/mcp_config.py
  - id: openwiki-source-b7ac60bca81274f953f09753
    resource: repo://server/agent/tools/incidents.py
  - id: openwiki-source-d9a6ad667cc247baf67de263
    resource: repo://server/agent/ws_chat.py
generated: { by: "claude-code", at: "2026-09-17T10:09:55.322Z" }
---

# Agent Extensibility: MCP, Plugins and Memory

The agent service ships with a fixed set of incident tools. Everything else a
user can give it — log search, documentation lookup, internal APIs, reusable
skills, standing instructions — arrives through four extension seams that all
converge on one place: the user's **workspace directory**, and the
`ClaudeAgentOptions` built for their WebSocket connection.

| Seam | Stored in | Reaches the agent as |
|---|---|---|
| Incident tools | Repository code | An in-process SDK MCP server |
| External MCP servers | `user_mcp_servers` table | Extra entries in `mcp_servers` |
| Skills / plugins | Supabase Storage, git repos | Files under the workspace `.claude/` |
| Memory | `claude_memory` table | `.claude/CLAUDE.md` in the workspace |

Related: [Session architecture](../ai-agent/session-architecture.md) ·
[Data model and migrations](../operations/data-and-migrations.md)

---

## The built-in tool server

Incident access is not an MCP subprocess. `tools/incidents.py` declares each
capability with the SDK's `@tool` decorator and packages them with
`create_sdk_mcp_server()`, so they run in the agent process and call the Go API
over HTTP. The set is deliberately small and read-oriented:
`get_incidents_by_time`, `get_incident_by_id`, `get_incident_stats`,
`get_current_time` and `search_incidents`.

Because the tools run in-process rather than as a child process, they need the
caller's identity without an MCP handshake to carry it. The module keeps the
auth token, org id and project id in module-level state with explicit setters
(`set_auth_token`, `set_org_id`, `set_project_id`), which the session populates
per connection before the first turn.

`build_options` seeds the MCP map with this server unconditionally, then layers
the user's own servers on top:

```python
mcp_servers = {"incident_tools": create_incident_tools_server()}
mcp_servers.update(normalize_mcp_servers(cfg.external_mcp))
```

The built-in server is therefore always present, and a user-configured server
can never displace it unless it collides on the name `incident_tools`.

---

## External MCP servers

### Postgres is the source of truth

User MCP servers live in the `user_mcp_servers` table, not in object storage.
`get_user_mcp_servers()` selects the rows for a user where `status = 'active'`
and shapes each row by its `server_type`:

- **`stdio`** → `{command, args, env}`, launched as a child process.
- **`sse`** / **`http`** → `{type, url, headers}`, reached over the network.
- Anything else is logged and skipped rather than passed through.

The function accepts either an `auth_token` or a direct `user_id`, with
`user_id` taking priority. That dual signature exists because the two WebSocket
endpoints authenticate differently: the JWT socket has a token to decode, while
the zero-trust socket has already established identity from a device
certificate and has no JWT to offer. See
[authentication and identity](../concepts/authentication-and-identity.md).

A failure to load servers is caught and returns `{}`. A broken MCP
configuration degrades the agent to its built-in tools; it does not fail the
connection.

### Normalizing before handing configs to the CLI

`normalize_mcp_servers()` sits between the stored rows and the SDK, and exists
to fix a specific incompatibility: `MCPToolManager.get_server_configs()`
annotates each config with a `tools` key listing what it discovered, which is
not part of the MCP config schema. The normalizer strips extra keys, tags stdio
servers explicitly with `type: "stdio"`, defaults URL-based servers to `http`,
passes through values that are already SDK server objects, and warns-and-skips
any entry with neither a `command` nor a `url`.

### The connection pool

Every WebSocket could start its own copy of every MCP subprocess. `MCPServerPool`
prevents that. It is an async singleton (`get_instance()` guards creation with a
class-level lock) holding four parallel maps: the live clients, the set of user
ids referencing each one, each one's last-access timestamp, and the reverse
index from user to server keys.

**Identity is the config, not the user.** `_make_server_key()` hashes the
command, the sorted args and the sorted env into a single string. Two users who
configure the same server with the same arguments and environment therefore
share one process. Anything that differs — a different API key in `env`, a
different argument — produces a different key and a separate process, which is
what keeps per-user credentials from leaking across tenants.

**Reference counting, not immediate shutdown.** `get_servers_for_user()` adds
the user id to the referencing set of each server it hands out;
`release_servers_for_user()` discards it. Dropping to zero references does not
stop the process — it only stamps `_last_access` with the current time, leaving
the server warm for the next session that wants the same config.

**Idle reaping is what actually stops processes.** A background task started
with the pool wakes every 60 seconds and calls `_cleanup_idle_servers()`, which
stops any server whose last access is older than `MCP_SERVER_IDLE_TIMEOUT`
(300 seconds by default) **and** whose reference set is empty. Both conditions
must hold, so a long-lived session is never reaped out from under an idle
conversation.

**Two limits bound the blast radius.** `MAX_MCP_SERVERS_PER_USER` (default 5)
caps what one user can start in a single call, and `MAX_GLOBAL_MCP_SERVERS`
(default 50) caps the pool. Hitting either logs a warning and stops starting
further servers — the manager is returned with the servers that did start, so
the session proceeds with partial tooling rather than failing outright.

### Background config sync

`streaming/mcp_config.py` maintains a separate, cache-backed view for the
workspace-file path. `MCPConfigCache` holds per-user configs with a TTL,
`MCPConfigManager` writes `.mcp.json` into the user's workspace, and a
background loop re-syncs registered users on `MCP_SYNC_INTERVAL` (60 seconds by
default). Users are explicitly registered and unregistered, so the loop only
does work for connections that exist.

---

## Marketplaces and plugins

### Why git replaced ZIP distribution

Marketplaces were originally downloaded as ZIP archives kept in object storage.
The current routes clone git repositories instead, and the module states the
reasons directly: incremental updates via `git fetch` are much faster than
re-downloading an archive, no ZIP files need to be stored in S3, and versioning
uses native git tooling. The practical consequence is that updating a large
skills repository transfers only the changed objects.

`POST /api/marketplace/clone` shallow-clones (`depth=1`) `owner/repo@branch`
into the user's workspace and records the marketplace row in Postgres.
`POST /api/marketplace/update` reads that row for the branch, verifies the
directory really is a git repository — refusing with an instruction to re-clone
if it is not, which is how ZIP-era directories are detected — and then fetches
and hard-resets to the remote branch, reporting whether anything changed and at
which commit.

Deletion is asynchronous: `DELETE /api/marketplace/{name}` hands off to
`cleanup_marketplace_task`, with a `deleting` status recorded so the UI can show
the transition rather than a half-removed marketplace.

### Name validation is a path-traversal guard

Every marketplace route checks the name against
`^[A-Za-z0-9_.-]+$` before it is used to build a directory path. Since the name
is attacker-controlled input that becomes a filesystem path under the user's
workspace, this pattern — which admits no `/` and no bare `..` segment — is what
keeps a clone or delete from escaping the workspace.

---

## Skills and memory

**Skills** are synced from a per-user Supabase Storage bucket. `sync_user_skills`
lists `.claude/skills/` in the bucket, downloads each file and extracts it into
`.claude/skills/` in the workspace — the same relative path at both ends, which
is what lets the agent discover them with no further configuration. Sync is
hash-based, so unchanged files are not re-downloaded.

**Memory** is a row, not a file. `sync_memory_to_workspace` reads `content` from
`claude_memory` for a `(user_id, scope)` pair and writes it to disk, where the
scope decides the destination: `local` writes `.claude/CLAUDE.md` inside the
user's workspace, `user` writes `~/.claude/CLAUDE.md`. A user with no memory row
yields an empty string, which is written anyway, so the file always reflects the
database rather than a stale earlier sync.

**Allowed tools** are also a table. `get_user_allowed_tools` reads the names a
user has pre-approved, and `ws_chat.build_session_config` loads them into
`SessionConfig.allowed_tools`; `build_options` passes them to the SDK so those
tools run without a permission round trip. See
[tool approval and security](../ai-agent/security-and-tool-approval.md) for what
happens to everything else.

---

## Workspace isolation

Each user gets a directory at `${USER_WORKSPACES_DIR}/{user_id}`, created by
`ensure_user_workspace()` and passed to the SDK as `cwd`. Everything above —
`.mcp.json`, cloned marketplaces, extracted skills, `CLAUDE.md` — lands inside
it, so the agent's working directory *is* the tenant boundary for files.

Two settings in `build_options` are load-bearing for that boundary:

- **`setting_sources` is `["project"]` only.** Adding `"user"` would make the
  SDK read `$HOME/.claude`, which in the container is `/root` — a single
  directory shared by every tenant. The narrower setting is what keeps one
  user's configuration from being read during another user's session.
- **`system_prompt` is passed as a preset with an `append`.** A bare string
  *replaces* the Claude Code preset, which silently drops the built-in Read,
  Write, Bash and Grep tools and leaves only the MCP ones.

Workspace preparation is deliberately non-fatal. `_workspace_for()` catches
failures, logs a warning and returns `None`, and the session runs without a
`cwd`. The agent still works; it simply cannot see the user's synced skills and
memory.
