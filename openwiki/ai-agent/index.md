# Files

- [Agent Extensibility: MCP, Plugins and Memory](extensibility.md) - How the InRes AI agent gains capabilities beyond its built-in incident tools - per-user MCP servers drawn from Postgres, git-cloned marketplaces, synced skills and CLAUDE.md memory - and how each user's workspace is kept isolated.
- [Agent Tool Approval, Zero Trust and Audit](security-and-tool-approval.md) - How InRes gates the AI agent's tool use behind human approval without deadlocking the WebSocket, how the zero-trust socket verifies every signed message independently, and how audit events are sanitized and batch-written.
- [AI Agent Session Architecture](session-architecture.md) - How the InRes agent holds one Claude Agent SDK client open for the life of a WebSocket - the four concurrent tasks, why a single task must own connect and disconnect, and why turns are serialised rather than cancelled.
- [Agent Streaming and WebSocket Event Contract](streaming-protocol.md) - How the Claude Agent SDK message stream is translated into the WebSocket events the InRes browser client consumes, and the invariants - no duplicate text, no subagent noise, exactly one terminal event - that keep the UI consistent.
