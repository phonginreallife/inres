"""
Memory Service - Persistent memory system for AI conversations.

This module provides claude-mem style memory management:
- Observations: Granular facts/preferences extracted from conversations
- Session Summaries: Compressed summaries of completed sessions
- Hybrid Search: FTS + importance scoring for retrieval

Architecture:
    User Conversation
           │
           ▼
    ┌─────────────────────┐
    │  Extract Observations│  ← PostToolUse, Stop hooks
    └──────────┬──────────┘
               │
               ▼
    ┌─────────────────────┐
    │  claude_observations │  ← PostgreSQL with FTS
    └──────────┬──────────┘
               │
               ▼
    ┌─────────────────────┐
    │   Hybrid Search     │  ← FTS + importance + recency
    └──────────┬──────────┘
               │
               ▼
    Progressive Disclosure  → Inject relevant context
"""

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from utils.database import execute_query

logger = logging.getLogger(__name__)


# ============================================
# Data Types
# ============================================

class ObservationType(str, Enum):
    """Types of observations that can be extracted from conversations."""
    FACT = "fact"           # Factual information about user/system
    PREFERENCE = "preference"  # User preferences
    CONTEXT = "context"     # Contextual information
    TOOL_RESULT = "tool_result"  # Important tool execution results
    INSIGHT = "insight"     # AI-generated insights


@dataclass
class Observation:
    """A single observation/memory extracted from conversation."""
    content: str
    observation_type: ObservationType = ObservationType.CONTEXT
    importance: float = 0.5  # 0.0 to 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None
    session_id: Optional[str] = None
    created_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "observation_type": self.observation_type.value if isinstance(self.observation_type, ObservationType) else self.observation_type,
            "importance": self.importance,
            "metadata": self.metadata,
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class SessionSummary:
    """Summary of a completed session."""
    session_id: str
    summary: str
    key_topics: List[str] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    message_count: int = 0
    token_count: int = 0
    duration_seconds: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None
    created_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "summary": self.summary,
            "key_topics": self.key_topics,
            "tools_used": self.tools_used,
            "message_count": self.message_count,
            "token_count": self.token_count,
            "duration_seconds": self.duration_seconds,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class SearchResult:
    """Result from hybrid search."""
    observation: Observation
    score: float  # Combined relevance score
    source: str   # 'fts' or 'recency' or 'importance'


# ============================================
# Observation CRUD Operations
# ============================================

async def save_observation(
    user_id: str,
    session_id: str,
    observation: Observation,
) -> Optional[str]:
    """
    Save an observation to the database.
    
    Args:
        user_id: User's UUID
        session_id: Session ID the observation belongs to
        observation: Observation to save
        
    Returns:
        Observation ID if saved successfully, None otherwise
    """
    try:
        observation_id = str(uuid.uuid4())
        
        execute_query(
            """
            INSERT INTO claude_observations 
            (id, user_id, session_id, observation_type, content, importance, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                observation_id,
                user_id,
                session_id,
                observation.observation_type.value if isinstance(observation.observation_type, ObservationType) else observation.observation_type,
                observation.content,
                observation.importance,
                json.dumps(observation.metadata),
            ),
            fetch="none"
        )
        
        logger.debug(f"Saved observation {observation_id} for user {user_id}")
        return observation_id
        
    except Exception as e:
        logger.error(f"Failed to save observation: {e}", exc_info=True)
        return None


async def save_observations_batch(
    user_id: str,
    session_id: str,
    observations: List[Observation],
) -> int:
    """
    Save multiple observations in batch.
    
    Args:
        user_id: User's UUID
        session_id: Session ID
        observations: List of observations to save
        
    Returns:
        Number of observations saved successfully
    """
    saved_count = 0
    for obs in observations:
        result = await save_observation(user_id, session_id, obs)
        if result:
            saved_count += 1
    
    logger.info(f"Saved {saved_count}/{len(observations)} observations for session {session_id}")
    return saved_count


async def get_observations_by_session(
    user_id: str,
    session_id: str,
    limit: int = 50,
) -> List[Observation]:
    """
    Get all observations for a specific session.
    
    Args:
        user_id: User's UUID
        session_id: Session ID
        limit: Maximum number of observations
        
    Returns:
        List of observations
    """
    try:
        results = execute_query(
            """
            SELECT id, session_id, observation_type, content, importance, metadata, created_at
            FROM claude_observations
            WHERE user_id = %s AND session_id = %s
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (user_id, session_id, limit),
            fetch="all"
        )
        
        observations = []
        for row in results or []:
            observations.append(Observation(
                id=str(row["id"]),
                session_id=row["session_id"],
                observation_type=row["observation_type"],
                content=row["content"],
                importance=row["importance"],
                metadata=row["metadata"] if isinstance(row["metadata"], dict) else {},
                created_at=row["created_at"],
            ))
        
        return observations
        
    except Exception as e:
        logger.error(f"Failed to get observations: {e}", exc_info=True)
        return []


async def get_recent_observations(
    user_id: str,
    limit: int = 20,
    observation_types: List[str] = None,
) -> List[Observation]:
    """
    Get recent observations for a user.
    
    Args:
        user_id: User's UUID
        limit: Maximum number of observations
        observation_types: Filter by observation types (optional)
        
    Returns:
        List of recent observations
    """
    try:
        if observation_types:
            results = execute_query(
                """
                SELECT id, session_id, observation_type, content, importance, metadata, created_at
                FROM claude_observations
                WHERE user_id = %s AND observation_type = ANY(%s)
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, observation_types, limit),
                fetch="all"
            )
        else:
            results = execute_query(
                """
                SELECT id, session_id, observation_type, content, importance, metadata, created_at
                FROM claude_observations
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_id, limit),
                fetch="all"
            )
        
        observations = []
        for row in results or []:
            observations.append(Observation(
                id=str(row["id"]),
                session_id=row["session_id"],
                observation_type=row["observation_type"],
                content=row["content"],
                importance=row["importance"],
                metadata=row["metadata"] if isinstance(row["metadata"], dict) else {},
                created_at=row["created_at"],
            ))
        
        return observations
        
    except Exception as e:
        logger.error(f"Failed to get recent observations: {e}", exc_info=True)
        return []


async def get_important_observations(
    user_id: str,
    min_importance: float = 0.7,
    limit: int = 10,
) -> List[Observation]:
    """
    Get high-importance observations for a user.
    
    Args:
        user_id: User's UUID
        min_importance: Minimum importance threshold (0.0-1.0)
        limit: Maximum number of observations
        
    Returns:
        List of important observations
    """
    try:
        results = execute_query(
            """
            SELECT id, session_id, observation_type, content, importance, metadata, created_at
            FROM claude_observations
            WHERE user_id = %s AND importance >= %s
            ORDER BY importance DESC, created_at DESC
            LIMIT %s
            """,
            (user_id, min_importance, limit),
            fetch="all"
        )
        
        observations = []
        for row in results or []:
            observations.append(Observation(
                id=str(row["id"]),
                session_id=row["session_id"],
                observation_type=row["observation_type"],
                content=row["content"],
                importance=row["importance"],
                metadata=row["metadata"] if isinstance(row["metadata"], dict) else {},
                created_at=row["created_at"],
            ))
        
        return observations
        
    except Exception as e:
        logger.error(f"Failed to get important observations: {e}", exc_info=True)
        return []


async def delete_observation(user_id: str, observation_id: str) -> bool:
    """
    Delete an observation.
    
    Args:
        user_id: User's UUID
        observation_id: Observation ID to delete
        
    Returns:
        True if deleted successfully
    """
    try:
        execute_query(
            "DELETE FROM claude_observations WHERE id = %s AND user_id = %s",
            (observation_id, user_id),
            fetch="none"
        )
        logger.info(f"Deleted observation {observation_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete observation: {e}", exc_info=True)
        return False


async def delete_session_observations(user_id: str, session_id: str) -> int:
    """
    Delete all observations for a session.
    
    Args:
        user_id: User's UUID
        session_id: Session ID
        
    Returns:
        Number of observations deleted
    """
    try:
        # First count
        count_result = execute_query(
            "SELECT COUNT(*) as count FROM claude_observations WHERE user_id = %s AND session_id = %s",
            (user_id, session_id),
            fetch="one"
        )
        count = count_result["count"] if count_result else 0
        
        # Then delete
        execute_query(
            "DELETE FROM claude_observations WHERE user_id = %s AND session_id = %s",
            (user_id, session_id),
            fetch="none"
        )
        
        logger.info(f"Deleted {count} observations for session {session_id}")
        return count
        
    except Exception as e:
        logger.error(f"Failed to delete session observations: {e}", exc_info=True)
        return 0


# ============================================
# Session Summary CRUD Operations
# ============================================

async def save_session_summary(
    user_id: str,
    summary: SessionSummary,
) -> Optional[str]:
    """
    Save a session summary to the database.
    
    Args:
        user_id: User's UUID
        summary: Session summary to save
        
    Returns:
        Summary ID if saved successfully, None otherwise
    """
    try:
        summary_id = str(uuid.uuid4())
        
        execute_query(
            """
            INSERT INTO claude_session_summaries
            (id, user_id, session_id, summary, key_topics, tools_used, message_count, token_count, duration_seconds, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET
                summary = EXCLUDED.summary,
                key_topics = EXCLUDED.key_topics,
                tools_used = EXCLUDED.tools_used,
                message_count = EXCLUDED.message_count,
                token_count = EXCLUDED.token_count,
                duration_seconds = EXCLUDED.duration_seconds,
                metadata = EXCLUDED.metadata
            """,
            (
                summary_id,
                user_id,
                summary.session_id,
                summary.summary,
                summary.key_topics,
                summary.tools_used,
                summary.message_count,
                summary.token_count,
                summary.duration_seconds,
                json.dumps(summary.metadata),
            ),
            fetch="none"
        )
        
        logger.info(f"Saved session summary for {summary.session_id}")
        return summary_id
        
    except Exception as e:
        logger.error(f"Failed to save session summary: {e}", exc_info=True)
        return None


async def get_session_summary(
    user_id: str,
    session_id: str,
) -> Optional[SessionSummary]:
    """
    Get summary for a specific session.
    
    Args:
        user_id: User's UUID
        session_id: Session ID
        
    Returns:
        Session summary or None
    """
    try:
        result = execute_query(
            """
            SELECT id, session_id, summary, key_topics, tools_used, message_count, token_count, duration_seconds, metadata, created_at
            FROM claude_session_summaries
            WHERE user_id = %s AND session_id = %s
            """,
            (user_id, session_id),
            fetch="one"
        )
        
        if not result:
            return None
        
        return SessionSummary(
            id=str(result["id"]),
            session_id=result["session_id"],
            summary=result["summary"],
            key_topics=result["key_topics"] or [],
            tools_used=result["tools_used"] or [],
            message_count=result["message_count"] or 0,
            token_count=result["token_count"] or 0,
            duration_seconds=result["duration_seconds"],
            metadata=result["metadata"] if isinstance(result["metadata"], dict) else {},
            created_at=result["created_at"],
        )
        
    except Exception as e:
        logger.error(f"Failed to get session summary: {e}", exc_info=True)
        return None


async def get_recent_summaries(
    user_id: str,
    limit: int = 5,
) -> List[SessionSummary]:
    """
    Get recent session summaries for a user.
    
    Args:
        user_id: User's UUID
        limit: Maximum number of summaries
        
    Returns:
        List of recent session summaries
    """
    try:
        results = execute_query(
            """
            SELECT id, session_id, summary, key_topics, tools_used, message_count, token_count, duration_seconds, metadata, created_at
            FROM claude_session_summaries
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (user_id, limit),
            fetch="all"
        )
        
        summaries = []
        for row in results or []:
            summaries.append(SessionSummary(
                id=str(row["id"]),
                session_id=row["session_id"],
                summary=row["summary"],
                key_topics=row["key_topics"] or [],
                tools_used=row["tools_used"] or [],
                message_count=row["message_count"] or 0,
                token_count=row["token_count"] or 0,
                duration_seconds=row["duration_seconds"],
                metadata=row["metadata"] if isinstance(row["metadata"], dict) else {},
                created_at=row["created_at"],
            ))
        
        return summaries
        
    except Exception as e:
        logger.error(f"Failed to get recent summaries: {e}", exc_info=True)
        return []


# ============================================
# Hybrid Search (FTS + Scoring)
# ============================================

async def search_observations_fts(
    user_id: str,
    query: str,
    limit: int = 10,
) -> List[SearchResult]:
    """
    Full-text search on observations.
    
    Uses PostgreSQL's ts_vector and ts_rank for relevance scoring.
    
    Args:
        user_id: User's UUID
        query: Search query
        limit: Maximum results
        
    Returns:
        List of SearchResults with FTS relevance scores
    """
    try:
        # Use the search_observations function from migration
        results = execute_query(
            "SELECT * FROM search_observations(%s, %s, %s)",
            (user_id, query, limit),
            fetch="all"
        )
        
        search_results = []
        for row in results or []:
            obs = Observation(
                id=str(row["id"]),
                session_id=row["session_id"],
                observation_type=row["observation_type"],
                content=row["content"],
                importance=row["importance"],
                created_at=row["created_at"],
            )
            search_results.append(SearchResult(
                observation=obs,
                score=float(row["rank"]) if row.get("rank") else 0.0,
                source="fts"
            ))
        
        return search_results
        
    except Exception as e:
        logger.error(f"FTS search failed: {e}", exc_info=True)
        return []


async def search_summaries_fts(
    user_id: str,
    query: str,
    limit: int = 5,
) -> List[SessionSummary]:
    """
    Full-text search on session summaries.
    
    Args:
        user_id: User's UUID
        query: Search query
        limit: Maximum results
        
    Returns:
        List of matching session summaries
    """
    try:
        results = execute_query(
            "SELECT * FROM search_session_summaries(%s, %s, %s)",
            (user_id, query, limit),
            fetch="all"
        )
        
        summaries = []
        for row in results or []:
            summaries.append(SessionSummary(
                id=str(row["id"]),
                session_id=row["session_id"],
                summary=row["summary"],
                key_topics=row["key_topics"] or [],
                created_at=row["created_at"],
            ))
        
        return summaries
        
    except Exception as e:
        logger.error(f"Summary FTS search failed: {e}", exc_info=True)
        return []


async def hybrid_search(
    user_id: str,
    query: str,
    limit: int = 10,
    weights: Dict[str, float] = None,
) -> List[SearchResult]:
    """
    Hybrid search combining FTS, importance, and recency.
    
    Scoring formula:
        score = (fts_weight * fts_rank) + (importance_weight * importance) + (recency_weight * recency_score)
    
    Args:
        user_id: User's UUID
        query: Search query
        limit: Maximum results
        weights: Custom weights for scoring factors
        
    Returns:
        List of SearchResults sorted by combined score
    """
    weights = weights or {
        "fts": 0.5,
        "importance": 0.3,
        "recency": 0.2,
    }
    
    try:
        # Get FTS results
        fts_results = await search_observations_fts(user_id, query, limit * 2)
        
        # Get important observations (may overlap with FTS)
        important_obs = await get_important_observations(user_id, min_importance=0.6, limit=limit)
        
        # Get recent observations
        recent_obs = await get_recent_observations(user_id, limit=limit)
        
        # Build a map of all observations with their scores
        obs_scores: Dict[str, Dict[str, float]] = {}
        
        # Add FTS scores
        max_fts = max((r.score for r in fts_results), default=1.0) or 1.0
        for result in fts_results:
            obs_id = result.observation.id
            if obs_id not in obs_scores:
                obs_scores[obs_id] = {"obs": result.observation, "fts": 0, "importance": 0, "recency": 0}
            obs_scores[obs_id]["fts"] = result.score / max_fts  # Normalize to 0-1
            obs_scores[obs_id]["obs"] = result.observation
        
        # Add importance scores
        for obs in important_obs:
            if obs.id not in obs_scores:
                obs_scores[obs.id] = {"obs": obs, "fts": 0, "importance": 0, "recency": 0}
            obs_scores[obs.id]["importance"] = obs.importance
            obs_scores[obs.id]["obs"] = obs
        
        # Add recency scores (newest = 1.0, oldest = 0.0)
        for i, obs in enumerate(recent_obs):
            if obs.id not in obs_scores:
                obs_scores[obs.id] = {"obs": obs, "fts": 0, "importance": 0, "recency": 0}
            recency_score = 1.0 - (i / max(len(recent_obs), 1))
            obs_scores[obs.id]["recency"] = recency_score
            obs_scores[obs.id]["obs"] = obs
        
        # Calculate combined scores
        results = []
        for obs_id, scores in obs_scores.items():
            combined_score = (
                weights["fts"] * scores["fts"] +
                weights["importance"] * scores["importance"] +
                weights["recency"] * scores["recency"]
            )
            results.append(SearchResult(
                observation=scores["obs"],
                score=combined_score,
                source="hybrid"
            ))
        
        # Sort by combined score
        results.sort(key=lambda r: r.score, reverse=True)
        
        return results[:limit]
        
    except Exception as e:
        logger.error(f"Hybrid search failed: {e}", exc_info=True)
        return []


# ============================================
# Observation Extraction
# ============================================

def extract_observations_from_response(
    response: str,
    session_id: str,
    tool_results: List[Dict[str, Any]] = None,
) -> List[Observation]:
    """
    Extract observations from an AI response.
    
    Uses heuristics to identify:
    - Facts mentioned about the user
    - User preferences expressed
    - Important contextual information
    - Tool results worth remembering
    
    Args:
        response: AI assistant's response text
        session_id: Current session ID
        tool_results: List of tool execution results (optional)
        
    Returns:
        List of extracted observations
    """
    observations = []
    
    # Extract tool results as observations
    if tool_results:
        for tool in tool_results:
            tool_name = tool.get("name", "unknown")
            tool_result = tool.get("result", "")
            
            # Only save significant tool results
            if len(tool_result) > 50 and len(tool_result) < 2000:
                observations.append(Observation(
                    content=f"Tool '{tool_name}' returned: {tool_result[:500]}...",
                    observation_type=ObservationType.TOOL_RESULT,
                    importance=0.6,
                    metadata={"tool_name": tool_name, "truncated": len(tool_result) > 500},
                    session_id=session_id,
                ))
    
    # Simple heuristics for extracting facts from response
    # Look for patterns like "I see that...", "You mentioned...", "Based on..."
    
    fact_patterns = [
        r"(?:I (?:see|notice|understand) that|You (?:mentioned|said|indicated)|Based on your|According to) (.+?)(?:\.|$)",
        r"(?:Your|The) (\w+ (?:is|are|was|were) .+?)(?:\.|$)",
    ]
    
    for pattern in fact_patterns:
        matches = re.findall(pattern, response, re.IGNORECASE)
        for match in matches[:3]:  # Limit to 3 per pattern
            if len(match) > 20 and len(match) < 500:
                observations.append(Observation(
                    content=match.strip(),
                    observation_type=ObservationType.FACT,
                    importance=0.5,
                    session_id=session_id,
                ))
    
    # Look for preference patterns
    preference_patterns = [
        r"(?:you (?:prefer|like|want)|your preference is) (.+?)(?:\.|$)",
    ]
    
    for pattern in preference_patterns:
        matches = re.findall(pattern, response, re.IGNORECASE)
        for match in matches[:2]:
            if len(match) > 10 and len(match) < 200:
                observations.append(Observation(
                    content=match.strip(),
                    observation_type=ObservationType.PREFERENCE,
                    importance=0.7,  # Preferences are more important
                    session_id=session_id,
                ))
    
    return observations


# ============================================
# Token Counting (Simple Estimation)
# ============================================

def estimate_tokens(text: str) -> int:
    """
    Estimate token count for a string.
    
    Uses simple heuristic: ~4 characters per token on average.
    
    Args:
        text: Text to estimate
        
    Returns:
        Estimated token count
    """
    if not text:
        return 0
    # Rough estimate: ~4 chars per token for English
    return len(text) // 4


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """
    Truncate text to approximately max_tokens.
    
    Args:
        text: Text to truncate
        max_tokens: Maximum tokens
        
    Returns:
        Truncated text
    """
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


# ============================================
# Memory Statistics
# ============================================

async def get_memory_stats(user_id: str) -> Dict[str, Any]:
    """
    Get memory statistics for a user.
    
    Args:
        user_id: User's UUID
        
    Returns:
        Dictionary with memory statistics
    """
    try:
        # Count observations by type
        obs_count = execute_query(
            """
            SELECT observation_type, COUNT(*) as count
            FROM claude_observations
            WHERE user_id = %s
            GROUP BY observation_type
            """,
            (user_id,),
            fetch="all"
        )
        
        # Count total observations
        total_obs = execute_query(
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
        
        # Average importance
        avg_importance = execute_query(
            "SELECT AVG(importance) as avg FROM claude_observations WHERE user_id = %s",
            (user_id,),
            fetch="one"
        )
        
        return {
            "total_observations": total_obs["count"] if total_obs else 0,
            "observations_by_type": {row["observation_type"]: row["count"] for row in (obs_count or [])},
            "total_summaries": summary_count["count"] if summary_count else 0,
            "average_importance": round(avg_importance["avg"], 2) if avg_importance and avg_importance["avg"] else 0,
        }
        
    except Exception as e:
        logger.error(f"Failed to get memory stats: {e}", exc_info=True)
        return {
            "total_observations": 0,
            "observations_by_type": {},
            "total_summaries": 0,
            "average_importance": 0,
        }


# ============================================
# AI-Powered Session Summarization
# ============================================

async def generate_ai_summary(
    messages: List[Dict[str, Any]],
    observations: List[Observation] = None,
    tools_used: List[str] = None,
    max_tokens: int = 500,
) -> Optional[SessionSummary]:
    """
    Generate a session summary using Claude AI.
    
    This provides higher quality summaries than heuristic extraction.
    Should be called at session end with the full message history.
    
    Args:
        messages: Full message history from the session
        observations: Observations extracted during the session
        tools_used: List of tools used during the session
        max_tokens: Maximum tokens for the summary
        
    Returns:
        SessionSummary with AI-generated content, or None on failure
    """
    import os
    
    try:
        import anthropic
        
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY not set, skipping AI summary")
            return None
        
        client = anthropic.AsyncAnthropic(api_key=api_key)
        
        # Build context from messages
        message_context = []
        for msg in messages[-20:]:  # Last 20 messages max
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Handle structured content
                text_parts = [
                    c.get("text", "") for c in content 
                    if isinstance(c, dict) and c.get("type") == "text"
                ]
                content = " ".join(text_parts)
            
            if content:
                message_context.append(f"{role.upper()}: {content[:500]}")
        
        # Build observations context
        obs_context = ""
        if observations:
            obs_texts = [f"- {obs.content}" for obs in observations[:10]]
            obs_context = f"\n\nKey observations extracted:\n" + "\n".join(obs_texts)
        
        # Build tools context
        tools_context = ""
        if tools_used:
            tools_context = f"\n\nTools used: {', '.join(tools_used)}"
        
        # Create summarization prompt
        prompt = f"""Summarize this AI conversation session concisely. Focus on:
1. Main topics discussed
2. Key decisions or conclusions
3. Important facts mentioned about the user
4. Any action items or follow-ups

Conversation:
{chr(10).join(message_context)}
{obs_context}
{tools_context}

Provide a 2-3 sentence summary and list 3-5 key topics."""
        
        # Call Claude for summarization
        response = await client.messages.create(
            model="claude-3-haiku-20240307",  # Use Haiku for cost efficiency
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        
        summary_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                summary_text += block.text
        
        if not summary_text:
            return None
        
        # Extract key topics (simple heuristic)
        key_topics = []
        lines = summary_text.split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith("-") or line.startswith("•"):
                topic = line.lstrip("-•").strip()
                if topic and len(topic) < 100:
                    key_topics.append(topic)
        
        # If no bullet points, extract from first sentence
        if not key_topics and summary_text:
            words = summary_text.split()[:10]
            if words:
                key_topics = [" ".join(words)]
        
        return SessionSummary(
            session_id="",  # Will be set by caller
            summary=truncate_to_tokens(summary_text, max_tokens),
            key_topics=key_topics[:10],
            tools_used=tools_used or [],
            message_count=len(messages),
            token_count=estimate_tokens(summary_text),
        )
        
    except ImportError:
        logger.warning("anthropic package not available for AI summarization")
        return None
    except Exception as e:
        logger.error(f"AI summarization failed: {e}", exc_info=True)
        return None


async def summarize_session_messages(
    user_id: str,
    session_id: str,
    use_ai: bool = True,
) -> Optional[SessionSummary]:
    """
    Summarize a session from its stored messages.
    
    Args:
        user_id: User's UUID
        session_id: Session ID to summarize
        use_ai: Whether to use AI for summarization
        
    Returns:
        SessionSummary or None
    """
    try:
        # Get messages for the session
        messages = execute_query(
            """
            SELECT role, content, message_type, tool_name, created_at
            FROM claude_messages
            WHERE conversation_id = %s
            ORDER BY created_at ASC
            """,
            (session_id,),
            fetch="all"
        )
        
        if not messages:
            logger.debug(f"No messages found for session {session_id}")
            return None
        
        # Get observations for the session
        observations = await get_observations_by_session(user_id, session_id)
        
        # Extract tools used
        tools_used = list(set(
            msg["tool_name"] for msg in messages 
            if msg.get("tool_name")
        ))
        
        # Convert to message format
        message_list = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages
        ]
        
        if use_ai:
            # Try AI summarization first
            summary = await generate_ai_summary(
                messages=message_list,
                observations=observations,
                tools_used=tools_used,
            )
            
            if summary:
                summary.session_id = session_id
                summary.message_count = len(messages)
                return summary
        
        # Fallback to heuristic summarization
        first_message = messages[0]["content"] if messages else ""
        last_message = messages[-1]["content"] if messages else ""
        
        summary_text = f"Session with {len(messages)} messages. "
        if first_message:
            summary_text += f"Started with: '{first_message[:100]}...' "
        if observations:
            obs_texts = [obs.content[:50] for obs in observations[:3]]
            summary_text += f"Key points: {'; '.join(obs_texts)}"
        
        return SessionSummary(
            session_id=session_id,
            summary=summary_text,
            key_topics=[],
            tools_used=tools_used,
            message_count=len(messages),
            token_count=estimate_tokens(summary_text),
        )
        
    except Exception as e:
        logger.error(f"Failed to summarize session: {e}", exc_info=True)
        return None
