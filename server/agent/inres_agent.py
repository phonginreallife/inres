"""
InRes production agent: Anthropic messages.stream + manual tool loop, MCP via ToolRouter.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import anthropic
from anthropic.types import ContentBlockDeltaEvent

from core.base_agent import AgentConfig, AgentFactory, BaseAgent
from core.lifecycle_hooks import (
    LifecycleHooksInterface,
    SessionContext,
    create_lifecycle_hooks,
    fire_and_forget,
)
from core.message_history import MessageHistory
from core.tool_approval import ToolApprovalPolicy, ToolApprovalSession
from streaming.mcp_client import MCPToolManager
from tool_router import ToolRouter
from tools import incidents as incident_tools
from tools import release as release_tools

logger = logging.getLogger(__name__)


@dataclass
class InResAgentConfig(AgentConfig):
    """Configuration for InResAgent (memory + turn limits)."""

    max_turns: int = 10
    enable_memory: bool = True
    enable_context_injection: bool = True
    enable_observation_extraction: bool = True
    enable_session_summary: bool = True
    context_token_budget: int = 2000


class InResAgent(BaseAgent):
    """
    Anthropic streaming agent with built-in incident/release tools and stdio MCP tools.
    """

    def __init__(
        self,
        config: Optional[InResAgentConfig] = None,
        api_key: Optional[str] = None,
        lifecycle_hooks: Optional[LifecycleHooksInterface] = None,
        mcp_manager: Optional[MCPToolManager] = None,
        approval_session: Optional[ToolApprovalSession] = None,
        approval_policy: Optional[ToolApprovalPolicy] = None,
    ):
        if config is None:
            config = InResAgentConfig()
        super().__init__(config)
        self.config: InResAgentConfig = config
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.client = anthropic.AsyncAnthropic(api_key=self.api_key)
        self._history = MessageHistory()
        self._mcp_manager = mcp_manager or MCPToolManager()
        builtin_handlers: Dict[str, Any] = {}
        builtin_handlers.update(incident_tools.INCIDENT_TOOL_HANDLERS)
        builtin_handlers.update(release_tools.RELEASE_TOOL_HANDLERS)
        builtin_schemas = list(incident_tools.INCIDENT_TOOL_SCHEMAS) + list(
            release_tools.RELEASE_TOOL_SCHEMAS
        )
        self._tool_router = ToolRouter(self._mcp_manager, builtin_handlers, builtin_schemas)
        if lifecycle_hooks is not None:
            self._hooks = lifecycle_hooks
        else:
            self._hooks = create_lifecycle_hooks(
                enable_memory=config.enable_memory,
                enable_context_injection=config.enable_context_injection,
                enable_observation_extraction=config.enable_observation_extraction,
                enable_session_summary=config.enable_session_summary,
                context_token_budget=config.context_token_budget,
            )
        self._auth_token: Optional[str] = None
        self._org_id: Optional[str] = None
        self._project_id: Optional[str] = None
        self._user_id: Optional[str] = None
        self._session_id: Optional[str] = None
        self._session_context: Optional[SessionContext] = None
        self._approval_session = approval_session
        self._approval_policy = approval_policy

    @property
    def tool_router(self) -> ToolRouter:
        return self._tool_router

    def set_auth_context(
        self,
        auth_token: Optional[str] = None,
        org_id: Optional[str] = None,
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self._auth_token = auth_token
        self._org_id = org_id
        self._project_id = project_id
        self._user_id = user_id
        self._session_id = session_id

    async def start_session(
        self,
        user_id: str,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionContext:
        self._user_id = user_id
        self._session_id = session_id
        self._session_context = await self._hooks.on_session_start(
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
        )
        logger.info("Session started: %s for user %s", session_id, user_id)
        return self._session_context

    async def end_session(self) -> None:
        if self._session_context:
            fire_and_forget(self._hooks.on_session_end(self._session_context))
            logger.info("Session ending: %s", self._session_id)
        self._session_context = None

    @property
    def messages(self) -> List[Dict[str, Any]]:
        return self._history.to_api_format()

    def _apply_tool_context_to_modules(self) -> None:
        token = self._auth_token or ""
        org = self._org_id or ""
        project = self._project_id or ""
        incident_tools.set_auth_token(token)
        incident_tools.set_org_id(org)
        incident_tools.set_project_id(project)
        release_tools.set_auth_token(token)
        release_tools.set_org_id(org)
        release_tools.set_project_id(project)

    async def process_message(
        self,
        prompt: str,
        output_queue: asyncio.Queue,
        tool_executor: Any = None,
        auth_token: Optional[str] = None,
        org_id: Optional[str] = None,
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        self.reset_interrupt()
        auth_token = auth_token or self._auth_token
        org_id = org_id or self._org_id
        project_id = project_id or self._project_id
        user_id = user_id or self._user_id
        self.set_auth_context(
            auth_token=auth_token, org_id=org_id, project_id=project_id, user_id=user_id
        )
        self._apply_tool_context_to_modules()

        effective_prompt = prompt
        if self._session_context:
            try:
                augmentation = await self._hooks.on_prompt_submit(
                    context=self._session_context,
                    prompt=prompt,
                    token_budget=self.config.context_token_budget,
                )
                effective_prompt = augmentation.augmented_prompt
            except Exception as e:
                logger.warning("Memory injection failed, using original prompt: %s", e)
                effective_prompt = prompt

        full_response = ""
        try:
            self.validate_and_fix_history()
            self._history.add_user_message(effective_prompt)
            tool_schemas = self._tool_router.get_tool_schemas()
            for turn in range(self.config.max_turns):
                if self._interrupted:
                    await output_queue.put({"type": "interrupted"})
                    break
                request_params: Dict[str, Any] = {
                    "model": self.config.model,
                    "max_tokens": self.config.max_tokens,
                    "system": self.config.system_prompt,
                    "messages": self._history.to_api_format(),
                    "temperature": self.config.temperature,
                }
                if tool_schemas:
                    request_params["tools"] = tool_schemas

                text_this_turn = ""
                async with self.client.messages.stream(**request_params) as stream:
                    async for event in stream:
                        if self._interrupted:
                            await output_queue.put({"type": "interrupted"})
                            break
                        if isinstance(event, ContentBlockDeltaEvent):
                            delta = event.delta
                            if hasattr(delta, "text") and delta.text:
                                text_this_turn += delta.text
                                full_response += delta.text
                                await output_queue.put({"type": "delta", "content": delta.text})

                    final_message = await stream.get_final_message()

                stop_reason = getattr(final_message, "stop_reason", None) or ""
                assistant_blocks: List[Dict[str, Any]] = []
                tool_uses: List[Dict[str, Any]] = []

                for block in final_message.content:
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
                        tool_uses.append(
                            {"id": block.id, "name": block.name, "input": block.input}
                        )

                if stop_reason != "tool_use" or not tool_uses:
                    if assistant_blocks:
                        if len(assistant_blocks) == 1 and assistant_blocks[0]["type"] == "text":
                            self._history.add_assistant_message(assistant_blocks[0]["text"])
                        else:
                            self._history.add_assistant_with_content(assistant_blocks)
                    elif text_this_turn:
                        self._history.add_assistant_message(text_this_turn)
                    await output_queue.put({"type": "complete"})
                    if self._session_context and full_response:
                        try:
                            fire_and_forget(
                                self._hooks.on_stop(
                                    context=self._session_context,
                                    response=full_response,
                                )
                            )
                        except Exception as e:
                            logger.warning("on_stop hook failed: %s", e)
                    return full_response

                results_for_history: List[Dict[str, Any]] = []
                for tu in tool_uses:
                    await output_queue.put(
                        {
                            "type": "tool_use",
                            "id": tu["id"],
                            "name": tu["name"],
                            "input": tu["input"],
                        }
                    )
                    need_approval = (
                        self._approval_session is not None
                        and self._approval_policy is not None
                        and self._approval_policy.needs_prompt(tu["name"])
                    )
                    if need_approval:
                        req_id = str(uuid.uuid4())
                        await output_queue.put(
                            {
                                "type": "permission_request",
                                "request_id": req_id,
                                "tool_name": tu["name"],
                                "input_data": tu["input"] or {},
                                "suggestions": [],
                            }
                        )
                        allowed = await self._approval_session.wait_for_decision(req_id)
                        if not allowed:
                            result_text = (
                                f"User denied execution of tool `{tu['name']}`. "
                                "The assistant should continue without this tool's result or try another approach."
                            )
                            is_err = True
                        else:
                            result_text, is_err = await self._tool_router.execute(
                                tu["name"], tu["input"] or {}
                            )
                    else:
                        result_text, is_err = await self._tool_router.execute(
                            tu["name"], tu["input"] or {}
                        )
                    await output_queue.put(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu["id"],
                            "content": result_text,
                            "is_error": is_err,
                        }
                    )
                    if self._session_context:
                        try:
                            await self._hooks.on_tool_use(
                                context=self._session_context,
                                tool_name=tu["name"],
                                tool_input=tu["input"] or {},
                                tool_result=result_text,
                                is_error=is_err,
                            )
                        except Exception as e:
                            logger.warning("on_tool_use hook failed: %s", e)
                    results_for_history.append(
                        {
                            "tool_use_id": tu["id"],
                            "result": result_text,
                            "is_error": is_err,
                        }
                    )

                self._history.add_assistant_with_content(assistant_blocks)
                self._history.add_tool_results(results_for_history)
            await output_queue.put(
                {
                    "type": "error",
                    "error": f"Exceeded maximum tool turns ({self.config.max_turns})",
                }
            )
            await output_queue.put({"type": "complete"})
            return full_response
        except Exception as e:
            logger.error("InResAgent error: %s", e, exc_info=True)
            await output_queue.put({"type": "error", "error": str(e)})
            return full_response

    def validate_and_fix_history(self) -> None:
        repaired = self._history.validate_and_repair()
        if repaired:
            logger.info("Conversation history was repaired for tool_use/tool_result pairing")

    def clear_history(self) -> None:
        self._history.clear()

    def set_history(self, messages: List[Dict[str, Any]]) -> None:
        self._history = MessageHistory(messages=messages)

    def get_history(self) -> List[Dict[str, Any]]:
        return self._history.to_api_format()

    def interrupt(self) -> None:
        self._interrupted = True
        if self._approval_session is not None:
            self._approval_session.cancel_all()

    def reset_interrupt(self) -> None:
        self._interrupted = False

    @property
    def hooks(self) -> LifecycleHooksInterface:
        return self._hooks

    @property
    def session_context(self) -> Optional[SessionContext]:
        return self._session_context

    @property
    def has_active_session(self) -> bool:
        return self._session_context is not None


AgentFactory.register("sdk_hybrid", InResAgent)
AgentFactory.register("inres", InResAgent)
