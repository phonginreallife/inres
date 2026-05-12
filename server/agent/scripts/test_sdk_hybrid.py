#!/usr/bin/env python3
"""Smoke-test InResAgent (SDKHybridAgent alias) from the agent directory."""

import asyncio
import os
import sys

agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, agent_dir)
os.chdir(agent_dir)


async def main() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    from hybrid import SDKHybridAgent, SDKHybridAgentConfig
    from streaming.mcp_client import MCPToolManager

    want_tools = "--tools" in sys.argv[1:]
    args = [a for a in sys.argv[1:] if a != "--tools"]
    if want_tools:
        prompt = "What is the current UTC time? Use get_current_time if available."
    else:
        prompt = " ".join(args) if args else "Hello! Reply in one short sentence."

    config = SDKHybridAgentConfig(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        max_turns=5,
        system_prompt="You are a concise DevOps assistant.",
    )
    agent = SDKHybridAgent(config=config, mcp_manager=MCPToolManager())
    q: asyncio.Queue = asyncio.Queue()

    async def drain() -> None:
        while True:
            ev = await q.get()
            if ev.get("type") == "delta":
                print(ev.get("content", ""), end="", flush=True)
            elif ev.get("type") == "tool_use":
                print(f"\n[tool_use {ev.get('name')}]", flush=True)
            elif ev.get("type") in ("complete", "error", "interrupted"):
                if ev.get("type") == "error":
                    print(f"\n[error] {ev.get('error')}", flush=True)
                break

    reader = asyncio.create_task(drain())
    await agent.process_message(prompt, q)
    await reader


if __name__ == "__main__":
    asyncio.run(main())
