"""Hierarchical Memory Architecture - Class brain-inspired layered memory.

Architecture:
  Working Memory (短期)   → LLM context window (current turn)
  Episodic Memory (本轮)  → Detailed round-by-round logs
  Semantic Memory (项目)  → Cross-project knowledge base
  Procedural Memory (习惯)→ Learned success patterns & preferences

This mirrors cognitive science models of human memory:
- Sensory → Short-term → Long-term declarative → Long-term procedural
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class MemoryEntry:
    """A single piece of stored memory."""
    entry_id: str
    content: str
    category: str  # "working" | "episodic" | "semantic" | "procedural"
    project_id: str
    created_at: float
    last_accessed: float
    access_count: int = 0
    importance: float = 0.5  # 0.0 to 1.0
    tags: List[str] = field(default_factory=list)
    embedding: Optional[List[float]] = None  # For semantic search
    
    @property
    def ttl(self) -> float:
        """Time-to-live in seconds based on importance."""
        if self.importance >= 0.8:
            return 7 * 24 * 3600  # 7 days
        elif self.importance >= 0.5:
            return 24 * 3600       # 1 day
        return 3600               # 1 hour
    
    def age(self) -> float:
        """Age in seconds."""
        return time.time() - self.created_at
    
    def is_expired(self) -> bool:
        """Check if memory entry has expired."""
        return self.age() > self.ttl
    
    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "content": self.content[:500],  # Truncate for storage
            "category": self.category,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "importance": self.importance,
            "tags": self.tags,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "MemoryEntry":
        return cls(
            entry_id=data["entry_id"],
            content=data["content"],
            category=data["category"],
            project_id=data["project_id"],
            created_at=data["created_at"],
            last_accessed=data["last_accessed"],
            access_count=data.get("access_count", 0),
            importance=data.get("importance", 0.5),
            tags=data.get("tags", []),
        )


@dataclass
class RecallResult:
    """Result of a memory recall operation."""
    entries: List[MemoryEntry]
    scores: List[float]
    recall_reason: str
    total_candidates: int = 0


# ============================================================================
# Active Forgetting Engine
# ============================================================================

class ActiveForgetting:
    """Implements active forgetting based on importance and recency.
    
    Memory decay follows an exponential model:
      retention(t) = e^(-λt) * importance
    
    Where λ is the decay rate (configurable per category).
    """
    
    # Decay rates per category (higher = faster forgetting)
    DECAY_RATES = {
        "working": 0.001,      # Very fast - lost after minutes
        "episodic": 0.0001,    # Fast - lost after hours  
        "semantic": 0.00001,   # Slow - preserved for days
        "procedural": 0.000001,  # Very slow - nearly permanent
    }
    
    # Threshold below which memory is considered forgotten
    FORGET_THRESHOLD = 0.01
    
    def __init__(self, decay_rates: Optional[Dict[str, float]] = None):
        if decay_rates:
            self.DECAY_RATES = {**self.DECAY_RATES, **decay_rates}
    
    def compute_retention(self, entry: MemoryEntry) -> float:
        """Compute retention score using exponential decay."""
        decay_rate = self.DECAY_RATES.get(entry.category, 0.0001)
        # Access count boosts retention slightly
        access_bonus = min(entry.access_count * 0.05, 0.3)
        retention = entry.importance * (1 + access_bonus)
        retention *= (retention * math.exp(-decay_rate * entry.age()))
        return retention
    
    def should_forget(self, entry: MemoryEntry) -> bool:
        """Decide whether to forget this memory entry."""
        retention = self.compute_retention(entry)
        return retention < self.FORGET_THRESHOLD
    
    def prioritize_recall(self, entries: List[MemoryEntry], 
                          query: str = "", limit: int = 10) -> List[Tuple[MemoryEntry, float]]:
        """Rank memory entries by relevance for recall.
        
        Score combines:
        1. Importance (static)
        2. Recency (time-decayed)
        3. Frequency (access count bonus)
        4. Semantic similarity (if query provided)
        """
        scored = []
        for entry in entries:
            # Base score from importance and recency
            base_score = self.compute_retention(entry)
            
            # Temporal bonus for recent access
            time_since_access = time.time() - entry.last_accessed
            recency_bonus = max(0, 1 - time_since_access / 3600)  # 1 hour half-life
            
            # Combined score
            final_score = base_score * 0.6 + recency_bonus * 0.4
            
            scored.append((entry, final_score))
        
        # Sort by score descending
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]


# ============================================================================
# Hierarchical Memory Manager
# ============================================================================

class HierarchicalMemory:
    """Layered memory system inspired by cognitive science.
    
    Four layers with different characteristics:
    
    1. Working Memory
       - Capacity: ~7±2 items (Miller's law)
       - Duration: Minutes (without rehearsal)
       - Purpose: Current task context
    
    2. Episodic Memory  
       - Capacity: Recent N rounds detailed log
       - Duration: Hours to days
       - Purpose: What happened, when, where
    
    3. Semantic Memory
       - Capacity: Unlimited (project knowledge base)
       - Duration: Days to weeks
       - Purpose: Facts, rules, general knowledge
    
    4. Procedural Memory
       - Capacity: Successful patterns library
       - Duration: Persistent (reinforced by use)
       - Purpose: "How to do things" - habits
    """
    
    # Capacity limits per layer
    WORKING_MEMORY_CAPACITY = 7
    EPISODIC_MEMORY_WINDOW = 20  # Last 20 rounds
    SEMANTIC_MEMORY_LIMIT = 100  # Top 100 most important facts
    PROCEDURAL_MEMORY_LIMIT = 50  # Top 50 successful patterns
    
    def __init__(self, project_id: str, db: Any, 
                 forgetting_engine: Optional[ActiveForgetting] = None):
        self.project_id = project_id
        self._db = db
        self.forgetting = forgetting_engine or ActiveForgetting()
        self._working_memory: List[MemoryEntry] = []
        self._episodic_cache: Dict[int, MemoryEntry] = {}
        
    # -------------------------------------------------------------------------
    # Working Memory Layer
    # -------------------------------------------------------------------------
    
    def push_working(self, content: str, importance: float = 0.5,
                     tags: Optional[List[str]] = None) -> str:
        """Add to working memory (current turn context).
        
        Replaces oldest entry if at capacity (LFU eviction).
        Returns the entry ID.
        """
        if len(self._working_memory) >= self.WORKING_MEMORY_CAPACITY:
            # Remove least frequently used
            self._working_memory.sort(key=lambda e: e.access_count)
            self._working_memory.pop(0)
        
        entry = MemoryEntry(
            entry_id=f"wm_{int(time.time() * 1000)}",
            content=content,
            category="working",
            project_id=self.project_id,
            created_at=time.time(),
            last_accessed=time.time(),
            importance=importance,
            tags=tags or [],
        )
        self._working_memory.append(entry)
        return entry.entry_id
    
    def recall_working(self, query: Optional[str] = None) -> List[str]:
        """Recall working memory contents."""
        if not self._working_memory:
            return []
        # Sort by recency
        self._working_memory.sort(key=lambda e: -e.last_accessed)
        return [e.content for e in self._working_memory]
    
    def clear_working(self) -> None:
        """Clear working memory (end of turn)."""
        self._working_memory.clear()
    
    # -------------------------------------------------------------------------
    # Episodic Memory Layer
    # -------------------------------------------------------------------------
    
    def store_episode(self, round_no: int, coder_result: str,
                      reviewer_verdict: dict, timestamp: Optional[float] = None) -> None:
        """Store a detailed episode record."""
        entry = MemoryEntry(
            entry_id=f"ep_{round_no}_{int(timestamp or time.time())}",
            content=json.dumps({
                "round": round_no,
                "coder_output": coder_result[:2000],
                "verdict": reviewer_verdict,
                "timestamp": timestamp or time.time(),
            }),
            category="episodic",
            project_id=self.project_id,
            created_at=timestamp or time.time(),
            last_accessed=timestamp or time.time(),
            importance=0.3,  # Moderate - may be recalled later
        )
        self._episodic_cache[round_no] = entry
        # Persist to DB
        try:
            self._db.store_memory_entry(entry.to_dict())
        except Exception:
            logger.debug("Failed to persist episodic memory")
    
    def recall_episodic(self, round_range: Tuple[int, int],
                        limit: int = 5) -> List[MemoryEntry]:
        """Recall episodes within a round range."""
        results = []
        for round_no in range(round_range[0], round_range[1] + 1):
            entry = self._episodic_cache.get(round_no)
            if entry and not entry.is_expired():
                entry.access_count += 1
                entry.last_accessed = time.time()
                results.append(entry)
        return results[:limit]
    
    # -------------------------------------------------------------------------
    # Semantic Memory Layer
    # -------------------------------------------------------------------------
    
    def store_semantic(self, content: str, importance: float = 0.5,
                       tags: Optional[List[str]] = None) -> str:
        """Store a semantic fact/rule."""
        entry = MemoryEntry(
            entry_id=f"sm_{int(time.time() * 1000)}",
            content=content,
            category="semantic",
            project_id=self.project_id,
            created_at=time.time(),
            last_accessed=time.time(),
            importance=importance,
            tags=tags or [],
        )
        try:
            self._db.store_memory_entry(entry.to_dict())
            # Boost importance for repeatedly accessed
            self._boost_importance(entry.entry_id, 0.1)
        except Exception:
            logger.debug("Failed to store semantic memory")
        return entry.entry_id
    
    def recall_semantic(self, query: str, limit: int = 10) -> List[MemoryEntry]:
        """Semantic search over stored facts."""
        try:
            entries = self._db.search_memories(
                project_id=self.project_id,
                category="semantic",
                query=query,
                limit=limit,
            )
            for e in entries:
                e.access_count += 1
                e.last_accessed = time.time()
            return entries
        except Exception:
            logger.debug("Failed to recall semantic memory")
            return []
    
    # -------------------------------------------------------------------------
    # Procedural Memory Layer
    # -------------------------------------------------------------------------
    
    def store_procedural(self, pattern_name: str, description: str,
                         success_count: int = 1, importance: float = 0.7) -> str:
        """Store a learned procedure/pattern."""
        entry = MemoryEntry(
            entry_id=f"pm_{pattern_name}_{int(time.time() * 1000)}",
            content=f"Pattern: {pattern_name}\n{description}",
            category="procedural",
            project_id=self.project_id,
            created_at=time.time(),
            last_accessed=time.time(),
            importance=min(importance + success_count * 0.05, 1.0),
            tags=["procedure", pattern_name.lower()],
        )
        try:
            self._db.store_memory_entry(entry.to_dict())
        except Exception:
            logger.debug("Failed to store procedural memory")
        return entry.entry_id
    
    def recall_procedural(self, context: str, limit: int = 5) -> List[MemoryEntry]:
        """Recall relevant procedures for current context."""
        try:
            entries = self._db.search_memories(
                project_id=self.project_id,
                category="procedural",
                query=context,
                limit=limit,
            )
            # Relevance scoring based on tag overlap
            context_tags = set(context.lower().split())
            for entry in entries:
                overlap = len(context_tags & set(entry.tags))
                entry.importance = max(entry.importance, overlap * 0.1)
            return entries
        except Exception:
            return []
    
    # -------------------------------------------------------------------------
    # Consolidation
    # -------------------------------------------------------------------------
    
    def consolidate(self) -> Dict[str, int]:
        """Consolidate memories across layers.
        
        Moves:
        - Working → Episodic (end of turn)
        - Old Episodic → Semantic (extract key facts)
        - Failed procedures → mark for re-evaluation
        """
        stats = {"working_promoted": 0, "semantic_extracted": 0, "episodic_compressed": 0}
        
        # Working → Episodic
        for entry in list(self._working_memory):
            if entry.age() > 300:  # 5 minutes
                self.store_episode(
                    round_no=int(entry.created_at),
                    coder_result=entry.content,
                    reviewer_verdict={},
                )
                self._working_memory.remove(entry)
                stats["working_promoted"] += 1
        
        # Expire old episodic entries
        expired = [
            e for e in self._episodic_cache.values()
            if e.is_expired()
        ]
        for entry in expired:
            del self._episodic_cache[entry.entry_id]
            stats["episodic_compressed"] += 1
        
        return stats
    
    # -------------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------------
    
    def _boost_importance(self, entry_id: str, delta: float) -> None:
        """Boost importance for reinforced memories."""
        try:
            self._db.update_memory_importance(entry_id, delta)
        except Exception:
            pass
    
    def get_summary(self) -> Dict[str, Any]:
        """Get memory layer statistics."""
        return {
            "working": {
                "count": len(self._working_memory),
                "capacity": self.WORKING_MEMORY_CAPACITY,
            },
            "episodic": {
                "count": len(self._episodic_cache),
                "window": self.EPISODIC_MEMORY_WINDOW,
            },
            "semantic": {
                "count": self._db.count_memories(self.project_id, "semantic")
                if hasattr(self._db, 'count_memories') else 0,
            },
            "procedural": {
                "count": self._db.count_memories(self.project_id, "procedural")
                if hasattr(self._db, 'count_memories') else 0,
            },
        }
