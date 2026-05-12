"""
Lifecycle Hooks - Claude-mem style hooks for AI conversation lifecycle.

This module provides hook points at key stages of AI conversation:
- SessionStart: When a new session begins
- UserPromptSubmit: Before processing user prompt
- PostToolUse: After a tool is executed
- Stop: When AI response completes
- SessionEnd: When session terminates

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                    Session Lifecycle                         │
    ├─────────────────────────────────────────────────────────────┤
    │                                                               │
    │  on_session_start()                                          │
    │       │                                                       │
    │       ▼                                                       │
    │  ┌─────────────────────────────────────────────────────────┐ │
    │  │              Message Loop                                │ │
    │  │  ┌─────────────────────────────────────────────────────┐│ │
    │  │  │ on_prompt_submit() → Retrieve & Inject Memories    ││ │
    │  │  │       │                                             ││ │
    │  │  │       ▼                                             ││ │
    │  │  │ [SDK Orchestrator / Direct API]                     ││ │
    │  │  │       │                                             ││ │
    │  │  │       ▼                                             ││ │
    │  │  │ on_tool_use() → Log tool execution (if tools used) ││ │
    │  │  │       │                                             ││ │
    │  │  │       ▼                                             ││ │
    │  │  │ on_stop() → Extract observations                    ││ │
    │  │  └─────────────────────────────────────────────────────┘│ │
    │  └─────────────────────────────────────────────────────────┘ │
    │       │                                                       │
    │       ▼                                                       │
    │  on_session_end() → Summarize session                        │
    │                                                               │
    └─────────────────────────────────────────────────────────────┘
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from services.memory_service import (
    Observation,
    ObservationType,
    SessionSummary,
    extract_observations_from_response,
    get_important_observations,
    get_recent_observations,
    get_recent_summaries,
    hybrid_search,
    save_observation,
    save_observations_batch,
    save_session_summary,
    estimate_tokens,
    truncate_to_tokens,
)

logger = logging.getLogger(__name__)


# ============================================
# Data Types
# ============================================

@dataclass
class SessionContext:
    """Context maintained throughout a session."""
    user_id: str
    session_id: str
    start_time: datetime = field(default_factory=datetime.utcnow)
    message_count: int = 0
    token_count: int = 0
    tools_used: List[str] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    observations: List[Observation] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PromptAugmentation:
    """Result of prompt augmentation with context."""
    original_prompt: str
    augmented_prompt: str
    injected_context: str
    context_tokens: int
    memories_used: int


@dataclass
class HookResult:
    """Generic result from a hook execution."""
    success: bool
    data: Any = None
    error: Optional[str] = None


# ============================================
# Abstract Hook Interface
# ============================================

class LifecycleHooksInterface(ABC):
    """
    Abstract interface for lifecycle hooks.
    
    Implementations can override individual hooks while
    using default behavior for others.
    """
    
    @abstractmethod
    async def on_session_start(
        self,
        user_id: str,
        session_id: str,
        metadata: Dict[str, Any] = None,
    ) -> SessionContext:
        """
        Called when a new session begins.
        
        Use this to:
        - Initialize session context
        - Load user preferences
        - Set up any session-specific state
        
        Args:
            user_id: User's UUID
            session_id: New session ID
            metadata: Additional session metadata
            
        Returns:
            SessionContext for the new session
        """
        pass
    
    @abstractmethod
    async def on_prompt_submit(
        self,
        context: SessionContext,
        prompt: str,
        token_budget: int = 2000,
    ) -> PromptAugmentation:
        """
        Called before processing user prompt.
        
        Use this to:
        - Retrieve relevant memories
        - Inject context into the prompt
        - Track message count
        
        Args:
            context: Current session context
            prompt: User's original prompt
            token_budget: Maximum tokens for context injection
            
        Returns:
            PromptAugmentation with context-enriched prompt
        """
        pass
    
    @abstractmethod
    async def on_tool_use(
        self,
        context: SessionContext,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_result: str,
        is_error: bool = False,
    ) -> None:
        """
        Called after a tool is executed.
        
        Use this to:
        - Track tools used in session
        - Extract observations from tool results
        - Update context with tool information
        
        Args:
            context: Current session context
            tool_name: Name of the tool executed
            tool_input: Input parameters for the tool
            tool_result: Result returned by the tool
            is_error: Whether the tool execution failed
        """
        pass
    
    @abstractmethod
    async def on_stop(
        self,
        context: SessionContext,
        response: str,
    ) -> List[Observation]:
        """
        Called when AI response completes.
        
        Use this to:
        - Extract observations from the response
        - Update token counts
        - Prepare for potential follow-up
        
        Args:
            context: Current session context
            response: Complete AI response text
            
        Returns:
            List of observations extracted from the response
        """
        pass
    
    @abstractmethod
    async def on_session_end(
        self,
        context: SessionContext,
    ) -> Optional[SessionSummary]:
        """
        Called when session terminates.
        
        Use this to:
        - Generate session summary
        - Persist collected observations
        - Clean up session state
        
        Args:
            context: Current session context
            
        Returns:
            SessionSummary if generated, None otherwise
        """
        pass


# ============================================
# Default Implementation
# ============================================

class MemoryLifecycleHooks(LifecycleHooksInterface):
    """
    Default implementation of lifecycle hooks with memory integration.
    
    Provides:
    - Progressive disclosure of context
    - Observation extraction
    - Session summarization
    - Fire-and-forget persistence
    """
    
    def __init__(
        self,
        enable_context_injection: bool = True,
        enable_observation_extraction: bool = True,
        enable_session_summary: bool = True,
        context_token_budget: int = 2000,
        max_observations_per_response: int = 5,
    ):
        """
        Initialize the hooks.
        
        Args:
            enable_context_injection: Whether to inject memories into prompts
            enable_observation_extraction: Whether to extract observations
            enable_session_summary: Whether to generate session summaries
            context_token_budget: Default token budget for context
            max_observations_per_response: Max observations to extract per response
        """
        self.enable_context_injection = enable_context_injection
        self.enable_observation_extraction = enable_observation_extraction
        self.enable_session_summary = enable_session_summary
        self.context_token_budget = context_token_budget
        self.max_observations_per_response = max_observations_per_response
        
        # Active sessions
        self._sessions: Dict[str, SessionContext] = {}
    
    async def on_session_start(
        self,
        user_id: str,
        session_id: str,
        metadata: Dict[str, Any] = None,
    ) -> SessionContext:
        """Initialize a new session context."""
        context = SessionContext(
            user_id=user_id,
            session_id=session_id,
            metadata=metadata or {},
        )
        
        self._sessions[session_id] = context
        logger.info(f"Session started: {session_id} for user {user_id}")
        
        return context
    
    async def on_prompt_submit(
        self,
        context: SessionContext,
        prompt: str,
        token_budget: int = None,
    ) -> PromptAugmentation:
        """
        Augment prompt with relevant context from memory.
        
        Progressive disclosure strategy:
        1. Recent session summary (if exists)
        2. Relevant observations via hybrid search
        3. Important high-priority memories
        """
        token_budget = token_budget or self.context_token_budget
        context.message_count += 1
        
        if not self.enable_context_injection:
            return PromptAugmentation(
                original_prompt=prompt,
                augmented_prompt=prompt,
                injected_context="",
                context_tokens=0,
                memories_used=0,
            )
        
        try:
            injected_parts = []
            remaining_tokens = token_budget
            memories_used = 0
            
            # 1. Get recent session summary
            summaries = await get_recent_summaries(context.user_id, limit=1)
            if summaries and summaries[0].session_id != context.session_id:
                summary_text = summaries[0].summary
                summary_tokens = estimate_tokens(summary_text)
                
                if summary_tokens <= remaining_tokens * 0.3:  # Max 30% for summary
                    injected_parts.append(f"## Previous Session Context\n{summary_text}")
                    remaining_tokens -= summary_tokens
                    memories_used += 1
            
            # 2. Search for relevant memories
            search_results = await hybrid_search(
                context.user_id,
                prompt,
                limit=10,
            )
            
            if search_results:
                relevant_memories = []
                for result in search_results:
                    mem_tokens = estimate_tokens(result.observation.content)
                    if mem_tokens <= remaining_tokens:
                        relevant_memories.append(f"- {result.observation.content}")
                        remaining_tokens -= mem_tokens
                        memories_used += 1
                    
                    if len(relevant_memories) >= 5:
                        break
                
                if relevant_memories:
                    injected_parts.append(
                        "## Relevant Context\n" + "\n".join(relevant_memories)
                    )
            
            # 3. Add high-importance memories if space remains
            if remaining_tokens > 200:
                important = await get_important_observations(
                    context.user_id,
                    min_importance=0.8,
                    limit=3,
                )
                
                # Filter out already included memories
                existing_content = " ".join(injected_parts)
                new_important = [
                    obs for obs in important
                    if obs.content not in existing_content
                ]
                
                if new_important:
                    important_text = "\n".join(
                        f"- {obs.content}" for obs in new_important[:2]
                    )
                    important_tokens = estimate_tokens(important_text)
                    
                    if important_tokens <= remaining_tokens:
                        injected_parts.append(
                            f"## Important Notes\n{important_text}"
                        )
                        memories_used += len(new_important[:2])
            
            # Build final injected context
            if injected_parts:
                injected_context = "\n\n".join(injected_parts)
                context_tokens = estimate_tokens(injected_context)
                
                # Prepend context to prompt
                augmented_prompt = f"""<context>
{injected_context}
</context>

{prompt}"""
                
                logger.debug(
                    f"Injected {memories_used} memories ({context_tokens} tokens) for session {context.session_id}"
                )
                
                return PromptAugmentation(
                    original_prompt=prompt,
                    augmented_prompt=augmented_prompt,
                    injected_context=injected_context,
                    context_tokens=context_tokens,
                    memories_used=memories_used,
                )
            
            # No context to inject
            return PromptAugmentation(
                original_prompt=prompt,
                augmented_prompt=prompt,
                injected_context="",
                context_tokens=0,
                memories_used=0,
            )
            
        except Exception as e:
            logger.error(f"Failed to augment prompt: {e}", exc_info=True)
            # Return original prompt on error
            return PromptAugmentation(
                original_prompt=prompt,
                augmented_prompt=prompt,
                injected_context="",
                context_tokens=0,
                memories_used=0,
            )
    
    async def on_tool_use(
        self,
        context: SessionContext,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_result: str,
        is_error: bool = False,
    ) -> None:
        """Track tool usage and optionally extract observations."""
        # Track tool in session
        if tool_name not in context.tools_used:
            context.tools_used.append(tool_name)
        
        # Store tool result
        context.tool_results.append({
            "name": tool_name,
            "input": tool_input,
            "result": tool_result,
            "is_error": is_error,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        logger.debug(f"Tool used in session {context.session_id}: {tool_name}")
    
    async def on_stop(
        self,
        context: SessionContext,
        response: str,
    ) -> List[Observation]:
        """Extract observations from the response."""
        # Update token count
        context.token_count += estimate_tokens(response)
        
        if not self.enable_observation_extraction:
            return []
        
        try:
            # Extract observations
            observations = extract_observations_from_response(
                response=response,
                session_id=context.session_id,
                tool_results=context.tool_results[-5:],  # Last 5 tool results
            )
            
            # Limit observations
            observations = observations[:self.max_observations_per_response]
            
            # Add to session context
            context.observations.extend(observations)
            
            # Fire-and-forget persistence
            if observations:
                asyncio.create_task(
                    self._persist_observations(context.user_id, context.session_id, observations)
                )
            
            return observations
            
        except Exception as e:
            logger.error(f"Failed to extract observations: {e}", exc_info=True)
            return []
    
    async def on_session_end(
        self,
        context: SessionContext,
    ) -> Optional[SessionSummary]:
        """Generate session summary and persist."""
        # Remove from active sessions
        self._sessions.pop(context.session_id, None)
        
        # Calculate duration
        duration_seconds = int(
            (datetime.utcnow() - context.start_time).total_seconds()
        )
        
        # Don't generate summary for very short sessions
        if context.message_count < 2:
            logger.debug(f"Skipping summary for short session {context.session_id}")
            return None
        
        if not self.enable_session_summary:
            return None
        
        try:
            # Generate summary
            summary = await self._generate_session_summary(context)
            
            if summary:
                summary.duration_seconds = duration_seconds
                
                # Fire-and-forget persistence
                asyncio.create_task(
                    self._persist_summary(context.user_id, summary)
                )
            
            logger.info(
                f"Session ended: {context.session_id} "
                f"(messages: {context.message_count}, duration: {duration_seconds}s)"
            )
            
            return summary
            
        except Exception as e:
            logger.error(f"Failed to generate session summary: {e}", exc_info=True)
            return None
    
    def get_session(self, session_id: str) -> Optional[SessionContext]:
        """Get active session context."""
        return self._sessions.get(session_id)
    
    async def _persist_observations(
        self,
        user_id: str,
        session_id: str,
        observations: List[Observation],
    ) -> None:
        """Background task to persist observations."""
        try:
            await save_observations_batch(user_id, session_id, observations)
            logger.debug(f"Persisted {len(observations)} observations for session {session_id}")
        except Exception as e:
            logger.error(f"Failed to persist observations: {e}", exc_info=True)
    
    async def _persist_summary(
        self,
        user_id: str,
        summary: SessionSummary,
    ) -> None:
        """Background task to persist session summary."""
        try:
            await save_session_summary(user_id, summary)
            logger.debug(f"Persisted summary for session {summary.session_id}")
        except Exception as e:
            logger.error(f"Failed to persist summary: {e}", exc_info=True)
    
    async def _generate_session_summary(
        self,
        context: SessionContext,
    ) -> Optional[SessionSummary]:
        """
        Generate a summary for the session.
        
        For now, this creates a simple summary from observations.
        In the future, this could use Claude to generate a proper summary.
        """
        try:
            # Extract key topics from observations
            key_topics = set()
            for obs in context.observations:
                # Simple topic extraction: first few words
                words = obs.content.split()[:5]
                if len(words) >= 3:
                    key_topics.add(" ".join(words[:3]))
            
            # Build summary text
            if context.observations:
                obs_summaries = [
                    obs.content[:100] for obs in context.observations[:5]
                ]
                summary_text = (
                    f"Session with {context.message_count} messages. "
                    f"Key observations: {'; '.join(obs_summaries)}"
                )
            else:
                summary_text = (
                    f"Session with {context.message_count} messages. "
                    f"Tools used: {', '.join(context.tools_used) if context.tools_used else 'None'}."
                )
            
            return SessionSummary(
                session_id=context.session_id,
                summary=truncate_to_tokens(summary_text, 500),
                key_topics=list(key_topics)[:10],
                tools_used=context.tools_used,
                message_count=context.message_count,
                token_count=context.token_count,
                metadata=context.metadata,
            )
            
        except Exception as e:
            logger.error(f"Failed to generate summary: {e}", exc_info=True)
            return None


# ============================================
# No-Op Implementation (for testing/bypass)
# ============================================

class NoOpLifecycleHooks(LifecycleHooksInterface):
    """
    No-op implementation that doesn't do anything.
    
    Useful for testing or when memory features are disabled.
    """
    
    async def on_session_start(
        self,
        user_id: str,
        session_id: str,
        metadata: Dict[str, Any] = None,
    ) -> SessionContext:
        return SessionContext(user_id=user_id, session_id=session_id)
    
    async def on_prompt_submit(
        self,
        context: SessionContext,
        prompt: str,
        token_budget: int = 2000,
    ) -> PromptAugmentation:
        return PromptAugmentation(
            original_prompt=prompt,
            augmented_prompt=prompt,
            injected_context="",
            context_tokens=0,
            memories_used=0,
        )
    
    async def on_tool_use(
        self,
        context: SessionContext,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_result: str,
        is_error: bool = False,
    ) -> None:
        pass
    
    async def on_stop(
        self,
        context: SessionContext,
        response: str,
    ) -> List[Observation]:
        return []
    
    async def on_session_end(
        self,
        context: SessionContext,
    ) -> Optional[SessionSummary]:
        return None


# ============================================
# Factory for creating hooks
# ============================================

def create_lifecycle_hooks(
    enable_memory: bool = True,
    **kwargs,
) -> LifecycleHooksInterface:
    """
    Factory function to create lifecycle hooks.
    
    Args:
        enable_memory: Whether to enable memory features
        **kwargs: Additional arguments for MemoryLifecycleHooks
        
    Returns:
        LifecycleHooksInterface implementation
    """
    if enable_memory:
        return MemoryLifecycleHooks(**kwargs)
    else:
        return NoOpLifecycleHooks()


# ============================================
# Fire-and-forget utility
# ============================================

def fire_and_forget(coro) -> asyncio.Task:
    """
    Schedule a coroutine to run in the background without blocking.
    
    Logs any exceptions that occur.
    
    Args:
        coro: Coroutine to run
        
    Returns:
        Task handle
    """
    task = asyncio.create_task(coro)
    
    def _handle_exception(t: asyncio.Task):
        try:
            exc = t.exception()
            if exc:
                logger.error(f"Background task failed: {exc}", exc_info=True)
        except asyncio.CancelledError:
            pass
    
    task.add_done_callback(_handle_exception)
    return task
