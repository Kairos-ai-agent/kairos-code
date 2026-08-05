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
    host: str = Field(default="0.0.0.0", alias="KAIROS_HOST")
    port: int = Field(default=8900, alias="KAIROS_PORT")
    debug: bool = Field(default=False, alias="KAIROS_DEBUG")

    # Paths
    workspace_dir: Path = Path("./workspace")
    data_dir: Path = Path("./data")

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
