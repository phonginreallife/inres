"""
Stateless Anthropic tool loop helpers (kept separate from ``inres_agent`` to avoid import cycles).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import anthropic

from core.message_history import MessageHistory
from tool_router import ToolRouter


async def run_anthropic_tools_nonstreaming(
    *,
    user_prompt: str,
    model: str,
    max_tokens: int,
    system_prompt: str,
    tool_router: ToolRouter,
    max_turns: int = 10,
    api_key: Optional[str] = None,
) -> str:
    """
    Run a bounded Anthropic tool loop without streaming (e.g. PGMQ background workers).
    Returns concatenated assistant text from the final model turn.
    """
    client = anthropic.AsyncAnthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))
    history = MessageHistory()
    history.add_user_message(user_prompt)
    tools = tool_router.get_tool_schemas()
    for _ in range(max_turns):
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=history.to_api_format(),
            tools=tools,
        )
        stop_reason = getattr(resp, "stop_reason", None) or ""
        assistant_blocks: List[Dict[str, Any]] = []
        tool_uses: List[Dict[str, Any]] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text" and getattr(block, "text", None):
                assistant_blocks.append({"type": "text", "text": block.text})
            elif btype == "tool_use":
                assistant_blocks.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
                tool_uses.append({"id": block.id, "name": block.name, "input": block.input})

        if stop_reason != "tool_use" or not tool_uses:
            return "".join(
                getattr(block, "text", "") or ""
                for block in resp.content
                if getattr(block, "type", None) == "text"
            )

        results_for_history: List[Dict[str, Any]] = []
        for tu in tool_uses:
            result_text, is_err = await tool_router.execute(tu["name"], tu["input"] or {})
            results_for_history.append(
                {"tool_use_id": tu["id"], "result": result_text, "is_error": is_err}
            )
        history.add_assistant_with_content(assistant_blocks)
        history.add_tool_results(results_for_history)

    return ""
