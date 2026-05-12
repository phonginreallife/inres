"""
Vector Store - ChromaDB integration for semantic search.

This module provides optional vector-based semantic search for observations,
complementing the PostgreSQL FTS with embedding-based similarity search.

Architecture:
    Observation
         │
         ▼
    ┌─────────────────────────────────────────────────┐
    │           Embedding Generation                   │
    │  ┌───────────────────────────────────────────┐  │
    │  │  Anthropic Embeddings (voyage-2 model)   │  │
    │  │  OR sentence-transformers (local)        │  │
    │  └───────────────────────────────────────────┘  │
    └─────────────────────┬───────────────────────────┘
                          │
                          ▼
    ┌─────────────────────────────────────────────────┐
    │              ChromaDB                            │
    │  ┌───────────────────────────────────────────┐  │
    │  │  Collection per user                      │  │
    │  │  • Store: observation_id, embedding       │  │
    │  │  • Query: nearest neighbors               │  │
    │  └───────────────────────────────────────────┘  │
    └─────────────────────┬───────────────────────────┘
                          │
                          ▼
    Hybrid Search = FTS + Vector Similarity

NOTE: This module is optional. If ChromaDB is not available,
the system falls back to PostgreSQL FTS only.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Try to import ChromaDB (optional dependency)
try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logger.info("ChromaDB not available - vector search disabled")


# ============================================
# Configuration
# ============================================

@dataclass
class VectorStoreConfig:
    """Configuration for vector store."""
    persist_directory: str = "./data/chromadb"
    collection_prefix: str = "inres_observations_"
    embedding_model: str = "all-MiniLM-L6-v2"  # sentence-transformers model
    use_anthropic_embeddings: bool = False
    anthropic_embedding_model: str = "voyage-2"
    distance_metric: str = "cosine"  # cosine, l2, ip


# ============================================
# Embedding Functions
# ============================================

class LocalEmbeddingFunction:
    """
    Local embedding function using sentence-transformers.
    
    Falls back to simple TF-IDF if sentence-transformers is not available.
    """
    
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None
        self._available = False
        
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name)
            self._available = True
            logger.info(f"Loaded sentence-transformers model: {model_name}")
        except ImportError:
            logger.warning("sentence-transformers not available, using fallback")
    
    def __call__(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for texts."""
        if self._available and self._model:
            embeddings = self._model.encode(texts)
            return embeddings.tolist()
        else:
            # Fallback: simple hash-based pseudo-embeddings
            # This is NOT suitable for production, just a fallback
            return [self._hash_embedding(text) for text in texts]
    
    def _hash_embedding(self, text: str, dim: int = 384) -> List[float]:
        """Generate a simple hash-based embedding (fallback only)."""
        import hashlib
        h = hashlib.sha256(text.encode()).hexdigest()
        # Convert hex to floats
        floats = []
        for i in range(0, min(len(h), dim * 2), 2):
            floats.append(int(h[i:i+2], 16) / 255.0)
        # Pad if necessary
        while len(floats) < dim:
            floats.append(0.0)
        return floats[:dim]


class AnthropicEmbeddingFunction:
    """
    Embedding function using Anthropic's embedding API.
    
    Note: Requires anthropic package and API key.
    """
    
    def __init__(self, model: str = "voyage-2"):
        self.model = model
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        self._available = bool(self.api_key)
        
        if not self._available:
            logger.warning("ANTHROPIC_API_KEY not set, Anthropic embeddings unavailable")
    
    def __call__(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using Anthropic API."""
        if not self._available:
            raise RuntimeError("Anthropic embeddings not available")
        
        # Note: As of 2024, Anthropic doesn't have a public embedding API
        # This is a placeholder for when/if they release one
        # For now, fall back to local embeddings
        logger.warning("Anthropic embedding API not yet available, using fallback")
        local_fn = LocalEmbeddingFunction()
        return local_fn(texts)


# ============================================
# Vector Store Manager
# ============================================

class VectorStore:
    """
    Vector store manager using ChromaDB.
    
    Provides:
    - Per-user collections for observation embeddings
    - Semantic similarity search
    - Integration with PostgreSQL observations
    """
    
    def __init__(self, config: VectorStoreConfig = None):
        """
        Initialize the vector store.
        
        Args:
            config: Vector store configuration
        """
        self.config = config or VectorStoreConfig()
        self._client = None
        self._embedding_fn = None
        self._collections: Dict[str, Any] = {}
        
        if not CHROMADB_AVAILABLE:
            logger.warning("ChromaDB not available - vector store disabled")
            return
        
        try:
            # Initialize ChromaDB client
            self._client = chromadb.Client(Settings(
                chroma_db_impl="duckdb+parquet",
                persist_directory=self.config.persist_directory,
                anonymized_telemetry=False,
            ))
            
            # Initialize embedding function
            if self.config.use_anthropic_embeddings:
                self._embedding_fn = AnthropicEmbeddingFunction(
                    model=self.config.anthropic_embedding_model
                )
            else:
                self._embedding_fn = LocalEmbeddingFunction(
                    model_name=self.config.embedding_model
                )
            
            logger.info(f"Vector store initialized at {self.config.persist_directory}")
            
        except Exception as e:
            logger.error(f"Failed to initialize vector store: {e}", exc_info=True)
            self._client = None
    
    @property
    def is_available(self) -> bool:
        """Check if vector store is available."""
        return self._client is not None
    
    def _get_collection(self, user_id: str):
        """Get or create a collection for a user."""
        if not self.is_available:
            return None
        
        collection_name = f"{self.config.collection_prefix}{user_id.replace('-', '_')}"
        
        if collection_name not in self._collections:
            self._collections[collection_name] = self._client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": self.config.distance_metric}
            )
        
        return self._collections[collection_name]
    
    async def add_observation(
        self,
        user_id: str,
        observation_id: str,
        content: str,
        metadata: Dict[str, Any] = None,
    ) -> bool:
        """
        Add an observation to the vector store.
        
        Args:
            user_id: User's UUID
            observation_id: Observation ID from PostgreSQL
            content: Observation content text
            metadata: Additional metadata
            
        Returns:
            True if added successfully
        """
        if not self.is_available:
            return False
        
        try:
            collection = self._get_collection(user_id)
            if not collection:
                return False
            
            # Generate embedding
            embeddings = self._embedding_fn([content])
            
            # Add to collection
            collection.add(
                ids=[observation_id],
                embeddings=embeddings,
                metadatas=[metadata or {}],
                documents=[content],
            )
            
            logger.debug(f"Added observation {observation_id} to vector store")
            return True
            
        except Exception as e:
            logger.error(f"Failed to add observation to vector store: {e}", exc_info=True)
            return False
    
    async def add_observations_batch(
        self,
        user_id: str,
        observations: List[Tuple[str, str, Dict[str, Any]]],
    ) -> int:
        """
        Add multiple observations in batch.
        
        Args:
            user_id: User's UUID
            observations: List of (observation_id, content, metadata) tuples
            
        Returns:
            Number of observations added
        """
        if not self.is_available or not observations:
            return 0
        
        try:
            collection = self._get_collection(user_id)
            if not collection:
                return 0
            
            ids = [obs[0] for obs in observations]
            contents = [obs[1] for obs in observations]
            metadatas = [obs[2] or {} for obs in observations]
            
            # Generate embeddings in batch
            embeddings = self._embedding_fn(contents)
            
            # Add to collection
            collection.add(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=contents,
            )
            
            logger.debug(f"Added {len(observations)} observations to vector store")
            return len(observations)
            
        except Exception as e:
            logger.error(f"Failed to add observations batch: {e}", exc_info=True)
            return 0
    
    async def search(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        Search for similar observations.
        
        Args:
            user_id: User's UUID
            query: Search query text
            limit: Maximum results
            min_score: Minimum similarity score (0-1 for cosine)
            
        Returns:
            List of results with observation_id, content, score, metadata
        """
        if not self.is_available:
            return []
        
        try:
            collection = self._get_collection(user_id)
            if not collection:
                return []
            
            # Generate query embedding
            query_embedding = self._embedding_fn([query])[0]
            
            # Search
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=limit,
                include=["documents", "metadatas", "distances"]
            )
            
            if not results or not results.get("ids"):
                return []
            
            # Format results
            formatted = []
            for i, obs_id in enumerate(results["ids"][0]):
                # Convert distance to similarity score
                distance = results["distances"][0][i] if results.get("distances") else 0
                
                # For cosine distance, similarity = 1 - distance
                if self.config.distance_metric == "cosine":
                    score = 1 - distance
                else:
                    score = 1 / (1 + distance)  # Generic conversion
                
                if score >= min_score:
                    formatted.append({
                        "observation_id": obs_id,
                        "content": results["documents"][0][i] if results.get("documents") else "",
                        "score": score,
                        "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                    })
            
            return formatted
            
        except Exception as e:
            logger.error(f"Vector search failed: {e}", exc_info=True)
            return []
    
    async def delete_observation(
        self,
        user_id: str,
        observation_id: str,
    ) -> bool:
        """
        Delete an observation from the vector store.
        
        Args:
            user_id: User's UUID
            observation_id: Observation ID to delete
            
        Returns:
            True if deleted
        """
        if not self.is_available:
            return False
        
        try:
            collection = self._get_collection(user_id)
            if not collection:
                return False
            
            collection.delete(ids=[observation_id])
            logger.debug(f"Deleted observation {observation_id} from vector store")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete observation from vector store: {e}", exc_info=True)
            return False
    
    async def delete_user_collection(self, user_id: str) -> bool:
        """
        Delete all vectors for a user.
        
        Args:
            user_id: User's UUID
            
        Returns:
            True if deleted
        """
        if not self.is_available:
            return False
        
        try:
            collection_name = f"{self.config.collection_prefix}{user_id.replace('-', '_')}"
            self._client.delete_collection(collection_name)
            self._collections.pop(collection_name, None)
            logger.info(f"Deleted vector collection for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete user collection: {e}", exc_info=True)
            return False
    
    def get_collection_stats(self, user_id: str) -> Dict[str, Any]:
        """
        Get statistics for a user's collection.
        
        Args:
            user_id: User's UUID
            
        Returns:
            Dictionary with collection statistics
        """
        if not self.is_available:
            return {"available": False, "count": 0}
        
        try:
            collection = self._get_collection(user_id)
            if not collection:
                return {"available": False, "count": 0}
            
            return {
                "available": True,
                "count": collection.count(),
                "name": collection.name,
            }
            
        except Exception as e:
            logger.error(f"Failed to get collection stats: {e}", exc_info=True)
            return {"available": False, "count": 0, "error": str(e)}


# ============================================
# Global Instance
# ============================================

_vector_store: Optional[VectorStore] = None


def get_vector_store(config: VectorStoreConfig = None) -> VectorStore:
    """
    Get or create the global vector store instance.
    
    Args:
        config: Optional configuration (only used on first call)
        
    Returns:
        VectorStore instance
    """
    global _vector_store
    
    if _vector_store is None:
        _vector_store = VectorStore(config)
    
    return _vector_store


def is_vector_search_available() -> bool:
    """Check if vector search is available."""
    return CHROMADB_AVAILABLE and get_vector_store().is_available


# ============================================
# Hybrid Search Integration
# ============================================

async def hybrid_search_with_vectors(
    user_id: str,
    query: str,
    fts_results: List[Dict[str, Any]],
    limit: int = 10,
    fts_weight: float = 0.6,
    vector_weight: float = 0.4,
) -> List[Dict[str, Any]]:
    """
    Combine FTS results with vector search results.
    
    Args:
        user_id: User's UUID
        query: Search query
        fts_results: Results from PostgreSQL FTS
        limit: Maximum results
        fts_weight: Weight for FTS scores
        vector_weight: Weight for vector scores
        
    Returns:
        Combined and re-ranked results
    """
    if not is_vector_search_available():
        # Return FTS results only
        return fts_results[:limit]
    
    try:
        store = get_vector_store()
        
        # Get vector search results
        vector_results = await store.search(
            user_id=user_id,
            query=query,
            limit=limit * 2,  # Get more for merging
        )
        
        if not vector_results:
            return fts_results[:limit]
        
        # Build combined scores map
        scores: Dict[str, Dict[str, float]] = {}
        
        # Add FTS scores
        max_fts = max((r.get("score", 0) for r in fts_results), default=1.0) or 1.0
        for result in fts_results:
            obs_id = result.get("observation_id") or result.get("id")
            if obs_id:
                scores[obs_id] = {
                    "fts": result.get("score", 0) / max_fts,
                    "vector": 0,
                    "data": result,
                }
        
        # Add vector scores
        for result in vector_results:
            obs_id = result.get("observation_id")
            if obs_id:
                if obs_id in scores:
                    scores[obs_id]["vector"] = result["score"]
                else:
                    scores[obs_id] = {
                        "fts": 0,
                        "vector": result["score"],
                        "data": result,
                    }
        
        # Calculate combined scores
        combined = []
        for obs_id, score_data in scores.items():
            combined_score = (
                fts_weight * score_data["fts"] +
                vector_weight * score_data["vector"]
            )
            combined.append({
                **score_data["data"],
                "observation_id": obs_id,
                "combined_score": combined_score,
                "fts_score": score_data["fts"],
                "vector_score": score_data["vector"],
            })
        
        # Sort by combined score
        combined.sort(key=lambda x: x["combined_score"], reverse=True)
        
        return combined[:limit]
        
    except Exception as e:
        logger.error(f"Hybrid search with vectors failed: {e}", exc_info=True)
        return fts_results[:limit]
