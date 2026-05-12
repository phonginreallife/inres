"""
Context Retriever - Progressive disclosure of memory context.

This module implements token-budgeted context retrieval for AI prompts,
ensuring relevant memories are injected without exceeding token limits.

Progressive Disclosure Strategy:
1. Recent session summary (if exists) - up to 30% of budget
2. Relevant observations via hybrid search - up to 50% of budget
3. User's CLAUDE.md preferences - remaining budget

Architecture:
    User Prompt
         │
         ▼
    ┌─────────────────────────────────────────────────┐
    │            Context Retriever                     │
    │  ┌───────────────────────────────────────────┐  │
    │  │  1. get_recent_summary()                  │  │
    │  │  2. hybrid_search(prompt)                 │  │
    │  │  3. get_user_memory()                     │  │
    │  │  4. get_important_observations()          │  │
    │  └───────────────────────────────────────────┘  │
    │                     │                            │
    │                     ▼                            │
    │  ┌───────────────────────────────────────────┐  │
    │  │  Token Budget Allocation                  │  │
    │  │  - Check remaining budget after each part │  │
    │  │  - Prioritize by relevance                │  │
    │  │  - Truncate if needed                     │  │
    │  └───────────────────────────────────────────┘  │
    └─────────────────────┬───────────────────────────┘
                          │
                          ▼
                 Augmented Prompt
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

from services.memory_service import (
    Observation,
    SessionSummary,
    SearchResult,
    get_important_observations,
    get_recent_observations,
    get_recent_summaries,
    hybrid_search,
    estimate_tokens,
    truncate_to_tokens,
)
from utils.database import execute_query

logger = logging.getLogger(__name__)


# ============================================
# Data Types
# ============================================

@dataclass
class ContextBudget:
    """Token budget allocation for context parts."""
    total: int = 2000
    summary_max: float = 0.3  # 30% for session summary
    search_max: float = 0.5  # 50% for search results
    memory_max: float = 0.2  # 20% for user memory
    important_max: float = 0.15  # 15% for important observations (from remaining)
    
    @property
    def summary_tokens(self) -> int:
        return int(self.total * self.summary_max)
    
    @property
    def search_tokens(self) -> int:
        return int(self.total * self.search_max)
    
    @property
    def memory_tokens(self) -> int:
        return int(self.total * self.memory_max)
    
    @property
    def important_tokens(self) -> int:
        return int(self.total * self.important_max)


@dataclass
class RetrievedContext:
    """Result of context retrieval."""
    context_text: str
    total_tokens: int
    parts: List[str]
    sources: dict  # {"summaries": 1, "observations": 5, "memory": 1}
    
    def to_xml(self) -> str:
        """Format as XML tags for prompt injection."""
        if not self.context_text:
            return ""
        return f"<context>\n{self.context_text}\n</context>"


# ============================================
# User Memory (CLAUDE.md) Retrieval
# ============================================

async def get_user_memory(user_id: str, scope: str = "local") -> Optional[str]:
    """
    Get user's CLAUDE.md content from database.
    
    Args:
        user_id: User's UUID
        scope: Memory scope ('local' or 'user')
        
    Returns:
        Memory content or None
    """
    try:
        result = execute_query(
            "SELECT content FROM claude_memory WHERE user_id = %s AND scope = %s",
            (user_id, scope),
            fetch="one"
        )
        
        if result and result.get("content"):
            return result["content"]
        return None
        
    except Exception as e:
        logger.error(f"Failed to get user memory: {e}", exc_info=True)
        return None


# ============================================
# Main Context Builder
# ============================================

async def build_context(
    user_id: str,
    prompt: str,
    budget: int = 2000,
    exclude_session_id: str = None,
) -> RetrievedContext:
    """
    Build context for prompt augmentation using progressive disclosure.
    
    Strategy:
    1. Get most recent session summary (if not current session)
    2. Search for relevant observations using hybrid search
    3. Add user's CLAUDE.md preferences
    4. Fill remaining with high-importance observations
    
    Args:
        user_id: User's UUID
        prompt: User's current prompt (for relevance search)
        budget: Total token budget for context
        exclude_session_id: Session ID to exclude from summaries (current session)
        
    Returns:
        RetrievedContext with assembled context
    """
    context_parts = []
    remaining_tokens = budget
    sources = {"summaries": 0, "observations": 0, "memory": 0, "important": 0}
    
    budget_config = ContextBudget(total=budget)
    
    try:
        # ==========================================
        # Part 1: Recent Session Summary
        # ==========================================
        summaries = await get_recent_summaries(user_id, limit=2)
        
        # Filter out current session
        if exclude_session_id:
            summaries = [s for s in summaries if s.session_id != exclude_session_id]
        
        if summaries:
            summary = summaries[0]
            summary_tokens = estimate_tokens(summary.summary)
            
            if summary_tokens <= budget_config.summary_tokens and summary_tokens <= remaining_tokens:
                # Format summary with key topics
                topics_str = ", ".join(summary.key_topics[:5]) if summary.key_topics else "N/A"
                summary_text = f"""## Previous Session Context
{summary.summary}

Key topics: {topics_str}"""
                
                context_parts.append(summary_text)
                remaining_tokens -= estimate_tokens(summary_text)
                sources["summaries"] = 1
                logger.debug(f"Added session summary ({estimate_tokens(summary_text)} tokens)")
        
        # ==========================================
        # Part 2: Relevant Observations (Hybrid Search)
        # ==========================================
        if remaining_tokens > 100:
            search_results = await hybrid_search(
                user_id=user_id,
                query=prompt,
                limit=15,
            )
            
            if search_results:
                observations_text = []
                obs_tokens = 0
                max_search_tokens = min(budget_config.search_tokens, remaining_tokens)
                
                for result in search_results:
                    obs_content = f"- {result.observation.content}"
                    content_tokens = estimate_tokens(obs_content)
                    
                    if obs_tokens + content_tokens <= max_search_tokens:
                        observations_text.append(obs_content)
                        obs_tokens += content_tokens
                        sources["observations"] += 1
                    else:
                        break
                
                if observations_text:
                    obs_section = "## Relevant Context\n" + "\n".join(observations_text)
                    context_parts.append(obs_section)
                    remaining_tokens -= estimate_tokens(obs_section)
                    logger.debug(f"Added {len(observations_text)} relevant observations ({obs_tokens} tokens)")
        
        # ==========================================
        # Part 3: User Memory (CLAUDE.md)
        # ==========================================
        if remaining_tokens > 100:
            user_memory = await get_user_memory(user_id)
            
            if user_memory:
                memory_tokens = estimate_tokens(user_memory)
                max_memory_tokens = min(budget_config.memory_tokens, remaining_tokens)
                
                if memory_tokens <= max_memory_tokens:
                    memory_section = f"## User Preferences\n{user_memory}"
                    context_parts.append(memory_section)
                    remaining_tokens -= estimate_tokens(memory_section)
                    sources["memory"] = 1
                    logger.debug(f"Added user memory ({memory_tokens} tokens)")
                else:
                    # Truncate if too long
                    truncated = truncate_to_tokens(user_memory, max_memory_tokens - 50)
                    memory_section = f"## User Preferences\n{truncated}"
                    context_parts.append(memory_section)
                    remaining_tokens -= estimate_tokens(memory_section)
                    sources["memory"] = 1
                    logger.debug(f"Added truncated user memory ({max_memory_tokens} tokens)")
        
        # ==========================================
        # Part 4: High-Importance Observations
        # ==========================================
        if remaining_tokens > 100:
            # Get high-importance observations not already included
            important_obs = await get_important_observations(
                user_id=user_id,
                min_importance=0.8,
                limit=5,
            )
            
            if important_obs:
                # Filter out already included observations
                existing_content = " ".join(context_parts)
                new_important = [
                    obs for obs in important_obs
                    if obs.content not in existing_content
                ]
                
                if new_important:
                    important_text = []
                    imp_tokens = 0
                    max_important_tokens = min(budget_config.important_tokens, remaining_tokens)
                    
                    for obs in new_important:
                        obs_content = f"- [Important] {obs.content}"
                        content_tokens = estimate_tokens(obs_content)
                        
                        if imp_tokens + content_tokens <= max_important_tokens:
                            important_text.append(obs_content)
                            imp_tokens += content_tokens
                            sources["important"] += 1
                        else:
                            break
                    
                    if important_text:
                        imp_section = "## Important Notes\n" + "\n".join(important_text)
                        context_parts.append(imp_section)
                        remaining_tokens -= estimate_tokens(imp_section)
                        logger.debug(f"Added {len(important_text)} important observations ({imp_tokens} tokens)")
        
        # ==========================================
        # Assemble Final Context
        # ==========================================
        if context_parts:
            context_text = "\n\n".join(context_parts)
            total_tokens = budget - remaining_tokens
            
            logger.info(
                f"Built context: {total_tokens} tokens, "
                f"sources={sources}"
            )
            
            return RetrievedContext(
                context_text=context_text,
                total_tokens=total_tokens,
                parts=context_parts,
                sources=sources,
            )
        
        # No context to return
        return RetrievedContext(
            context_text="",
            total_tokens=0,
            parts=[],
            sources=sources,
        )
        
    except Exception as e:
        logger.error(f"Failed to build context: {e}", exc_info=True)
        return RetrievedContext(
            context_text="",
            total_tokens=0,
            parts=[],
            sources=sources,
        )


# ============================================
# Prompt Augmentation
# ============================================

async def augment_prompt(
    user_id: str,
    prompt: str,
    token_budget: int = 2000,
    exclude_session_id: str = None,
) -> str:
    """
    Augment a prompt with relevant context.
    
    Args:
        user_id: User's UUID
        prompt: Original user prompt
        token_budget: Maximum tokens for context
        exclude_session_id: Current session ID to exclude
        
    Returns:
        Augmented prompt with context prepended
    """
    context = await build_context(
        user_id=user_id,
        prompt=prompt,
        budget=token_budget,
        exclude_session_id=exclude_session_id,
    )
    
    if context.context_text:
        return f"{context.to_xml()}\n\n{prompt}"
    
    return prompt


# ============================================
# Context Stats
# ============================================

async def get_context_stats(user_id: str) -> dict:
    """
    Get statistics about available context for a user.
    
    Args:
        user_id: User's UUID
        
    Returns:
        Dictionary with context statistics
    """
    try:
        # Count observations
        obs_count = execute_query(
            "SELECT COUNT(*) as count FROM claude_observations WHERE user_id = %s",
            (user_id,),
            fetch="one"
        )
        
        # Count summaries
        summary_count = execute_query(
            "SELECT COUNT(*) as count FROM claude_session_summaries WHERE user_id = %s",
            (user_id,),
            fetch="one"
        )
        
        # Check if user has memory
        memory = await get_user_memory(user_id)
        
        return {
            "observations_count": obs_count["count"] if obs_count else 0,
            "summaries_count": summary_count["count"] if summary_count else 0,
            "has_user_memory": bool(memory),
            "user_memory_tokens": estimate_tokens(memory) if memory else 0,
        }
        
    except Exception as e:
        logger.error(f"Failed to get context stats: {e}", exc_info=True)
        return {
            "observations_count": 0,
            "summaries_count": 0,
            "has_user_memory": False,
            "user_memory_tokens": 0,
        }
