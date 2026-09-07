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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

settings = Settings()
