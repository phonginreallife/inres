#!/usr/bin/env python3
"""Local smoke test for InResAgent (replaces legacy HybridAgent / StreamingAgent scripts)."""

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

    from hybrid import InResAgent, InResAgentConfig
    from streaming.mcp_client import MCPToolManager

    prompt = sys.argv[1] if len(sys.argv) > 1 else "Hello! What can you help me with?"
    config = InResAgentConfig(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        max_turns=8,
        system_prompt="You are a helpful AI assistant for DevOps and incident response.",
    )
    agent = InResAgent(config=config, mcp_manager=MCPToolManager())
    output_queue: asyncio.Queue = asyncio.Queue()

    async def process():
        return await agent.process_message(prompt=prompt, output_queue=output_queue)

    task = asyncio.create_task(process())
    print("Streaming Response:")
    print("-" * 60)
    while True:
        try:
            event = await asyncio.wait_for(output_queue.get(), timeout=0.15)
        except asyncio.TimeoutError:
            if task.done():
                break
            continue
        if event["type"] == "delta":
            print(event["content"], end="", flush=True)
        elif event["type"] == "tool_use":
            print(f"\n[Tool: {event['name']}]", flush=True)
        elif event["type"] == "complete":
            print()
            break
        elif event["type"] == "error":
            print(f"\n[ERROR: {event['error']}]")
            break
    result = await task
    print("-" * 60)
    print(f"Done ({len(result)} chars)")


if __name__ == "__main__":
    asyncio.run(main())
