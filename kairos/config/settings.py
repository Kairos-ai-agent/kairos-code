"""Global settings for Kairos Code."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings

class LLMProviderConfig(BaseSettings):
    """Configuration for a single LLM provider."""

    api_key: str = ""
    base_url: Optional[str] = None
    model: str = "gpt-4o"
    max_tokens: int = 8192
    temperature: float = 0.7
    timeout: int = 120

class LoopGateConfig(BaseSettings):
    """Configuration for loop termination gates."""

    # Approval threshold: minimum score to approve a round
    approve_score_threshold: int = Field(default=75, alias="LOOP_APPROVE_THRESHOLD")
    
    # No-progress detection: how many consecutive identical issue signatures trigger stop
    no_progress_limit: int = Field(default=5, alias="LOOP_NO_PROGRESS_LIMIT")
    
    # Safety cap: hard maximum number of rounds (defensive backstop)
    safety_cap: int = Field(default=50, alias="LOOP_SAFETY_CAP")
    
    # Per-round timeout in seconds
    per_round_timeout_s: float = Field(default=600.0, alias="LOOP_PER_ROUND_TIMEOUT")
    
    # Plan approval timeout in seconds
    approval_timeout_s: float = Field(default=90.0, alias="LOOP_APPROVAL_TIMEOUT")
    
    # Cost caps
    cost_token_cap: int = Field(default=500_000, alias="LOOP_COST_TOKEN_CAP")
    cost_time_cap_s: int = Field(default=30 * 60, alias="LOOP_COST_TIME_CAP")
    
    # Infra failure limit: consecutive Reviewer failures before stopping
    infra_failure_limit: int = Field(default=5, alias="LOOP_INFRA_FAILURE_LIMIT")
    
    # Stagnation detection
    stagnation_window: int = Field(default=3, alias="LOOP_STAGNATION_WINDOW")
    stagnation_tolerance: int = Field(default=2, alias="LOOP_STAGNATION_TOLERANCE")
    
    # Adaptive caps
    heavy_requirement_chars: int = Field(default=2000, alias="LOOP_HEAVY_REQ_CHARS")
    heavy_plan_items: int = Field(default=8, alias="LOOP_HEAVY_PLAN_ITEMS")
    max_safety_cap: int = Field(default=200, alias="LOOP_MAX_SAFETY_CAP")
    max_token_cap: int = Field(default=2_000_000, alias="LOOP_MAX_TOKEN_CAP")
    
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

class CoderConfig(BaseSettings):
    """Configuration for Coder agent behavior."""

    # Max tool turns per task (soft limit, agent continues if needed)
    max_tool_turns: int = Field(default=200, alias="CODER_MAX_TOOL_TURNS")
    
    # Max chat turns for direct conversation
    max_chat_turns: int = Field(default=10, alias="CODER_MAX_CHAT_TURNS")
    
    # Memory management
    max_tokens: int = Field(default=80000, alias="CODER_MAX_TOKENS")
    keep_recent: int = Field(default=4, alias="CODER_KEEP_RECENT")
    summarize_every_n: int = Field(default=8, alias="CODER_SUMMARIZE_EVERY_N")
    
    # Temperature schedule
    temperature_start: float = Field(default=0.7, alias="CODER_TEMP_START")
    temperature_end: float = Field(default=0.15, alias="CODER_TEMP_END")
    temperature_decay_rounds: int = Field(default=15, alias="CODER_TEMP_DECAY_ROUNDS")
    
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

class ReviewerConfig(BaseSettings):
    """Configuration for Reviewer agent behavior."""

    # Max tool turns - tighter than Coder since reviewer is read-only
    max_tool_turns: int = Field(default=20, alias="REVIEWER_MAX_TOOL_TURNS")
    
    # Max chat turns
    max_chat_turns: int = Field(default=5, alias="REVIEWER_MAX_CHAT_TURNS")
    
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

class PlanModeConfig(BaseSettings):
    """Configuration for plan mode behavior."""

    # Auto-approve threshold: requirements shorter than this skip plan review
    auto_approve_char_limit: int = Field(default=200, alias="PLAN_AUTO_APPROVE_CHARS")
    
    # Allow multi-line short requirements to bypass auto-approve
    require_multi_line_for_auto: bool = Field(default=True, alias="PLAN_REQUIRE_MULTI_LINE")
    
    # Disable auto-approve entirely (require manual approval for all plans)
    disable_auto_approve: bool = Field(default=False, alias="PLAN_DISABLE_AUTO_APPROVE")
    
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

class Settings(BaseSettings):
    """Global application settings."""

    # Server
    # SECURITY: default to a loopback bind so the API is only reachable
    # from this machine. To expose to the LAN/network, set KAIROS_HOST
    # explicitly (and set KAIROS_API_TOKEN — see api/auth.py).
    host: str = Field(default="127.0.0.1", alias="KAIROS_HOST")
    port: int = Field(default=8900, alias="KAIROS_PORT")
    debug: bool = Field(default=False, alias="KAIROS_DEBUG")
    # Number of uvicorn worker processes. 0 = auto-pick
    # ``min(8, 2 * cpu + 1)`` per the textbook formula (capped
    # to keep memory bounded). Set to 1 in tests / single-machine
    # dev to avoid forking a process pool you don't need.
    workers: int = Field(default=0, alias="KAIROS_WORKERS")
    # Loop implementation: "auto" picks uvloop on POSIX, the
    # built-in asyncio loop on Windows. "uvloop" forces uvloop
    # even on Windows (will raise); "asyncio" disables.
    loop: str = Field(default="auto", alias="KAIROS_LOOP")

    # Paths — anchored (absolute), NOT CWD-relative. The backend is
    # launched from different working directories by different
    # launchers; a CWD-relative data_dir made every restart load a
    # DIFFERENT kairos.db / settings file (empty or foreign), which
    # surfaced as "Project not found: <id>" and "LLM settings lost
    # after restart". KAIROS_DATA_DIR overrides the data dir (the
    # packaged exe sets it to <exe>/data); workspace_dir lives next
    # to data_dir so workspaces follow the data location too.
    data_dir: Path = Path(
        os.environ.get("KAIROS_DATA_DIR",
                       Path(__file__).resolve().parent.parent.parent / "data")
    )
    workspace_dir: Path = data_dir.parent / "workspace"

    # CORS: comma-separated list of allowed origins. Default covers the
    # two localhost dev addresses; set KAIROS_CORS_ORIGINS to add more.
    # Note: when allow_credentials=True, browsers reject "*" — list the
    # exact origins explicitly.
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        alias="KAIROS_CORS_ORIGINS",
    )

    # LLM Providers
    openai: LLMProviderConfig = Field(default_factory=lambda: LLMProviderConfig(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=os.getenv("OPENAI_BASE_URL"),
        model="gpt-4o",
    ))
    anthropic: LLMProviderConfig = Field(default_factory=lambda: LLMProviderConfig(
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        model="claude-sonnet-4-20250514",
    ))
    deepseek: LLMProviderConfig = Field(default_factory=lambda: LLMProviderConfig(
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
    ))
    ollama: LLMProviderConfig = Field(default_factory=lambda: LLMProviderConfig(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        model="llama3.1",
    ))
    dashscope: LLMProviderConfig = Field(default_factory=lambda: LLMProviderConfig(
        api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        model="qwen-max",
    ))
    zhipuai: LLMProviderConfig = Field(default_factory=lambda: LLMProviderConfig(
        api_key=os.getenv("ZHIPUAI_API_KEY", ""),
        model="glm-4",
    ))
    gemini: LLMProviderConfig = Field(default_factory=lambda: LLMProviderConfig(
        api_key=os.getenv("GEMINI_API_KEY", ""),
        model="gemini-2.0-flash",
    ))
    openrouter: LLMProviderConfig = Field(default_factory=lambda: LLMProviderConfig(
        api_key=os.getenv("OPENROUTER_API_KEY", ""),
        base_url="https://openrouter.ai/api/v1",
        model="anthropic/claude-sonnet-4-20250514",
    ))

    # Default model for new agents
    default_provider: str = "openai"
    
    # Feature flags and subsystem configs
    loop_gates: LoopGateConfig = Field(default_factory=LoopGateConfig)
    coder: CoderConfig = Field(default_factory=CoderConfig)
    reviewer: ReviewerConfig = Field(default_factory=ReviewerConfig)
    plan_mode: PlanModeConfig = Field(default_factory=PlanModeConfig)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

settings = Settings()
