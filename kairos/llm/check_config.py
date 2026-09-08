"""LLM Provider Configuration Status Report

This module checks and reports the status of all configured LLM providers.
Run: python kairos/llm/check_config.py
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def load_settings() -> dict:
    """Load settings.json."""
    data_dir = Path(__file__).parent.parent.parent / "data"
    settings_file = data_dir / "settings.json"
    if settings_file.exists():
        with open(settings_file, encoding="utf-8") as f:
            return json.load(f)
    return {}


def check_openai_connection(api_key: str, base_url: str, model: str) -> Dict:
    """Test OpenAI-compatible provider connection."""
    from kairos.llm.providers.openai_provider import OpenAIProvider
    from kairos.llm.base import LLMConfig, LLMMessage
    
    config = LLMConfig(
        provider="openai",
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_tokens=30,
        temperature=0.7,
    )
    
    provider = OpenAIProvider(config)
    try:
        result = asyncio.run(provider.complete([
            LLMMessage(role="user", content="Reply with just: OK")
        ]))
        return {
            "status": "connected",
            "model": result.model,
            "response": result.content[:100],
            "tokens": result.usage.get("total_tokens", 0),
        }
    except Exception as e:
        return {
            "status": "error",
            "error_type": type(e).__name__,
            "error_msg": str(e)[:300],
        }


def main():
    """Main diagnostic routine."""
    print("=" * 60)
    print("Kairos Code LLM Connectivity Diagnostic")
    print("=" * 60)
    print()
    
    settings = load_settings()
    
    # Check custom models
    custom_models = settings.get("custom_models", [])
    print(f"Custom models defined: {len(custom_models)}")
    for cm in custom_models:
        has_key = bool(cm.get("api_key"))
        print(f"  - {cm.get('name', '?')}: key={'SET' if has_key else 'NOT SET'} model={cm.get('model')} url={cm.get('base_url')}")
    print()
    
    # Check role mappings
    role_mappings = settings.get("role_mappings", {})
    print(f"Role mappings: {role_mappings}")
    print()
    
    # Test connections
    print("--- Testing Connections ---")
    
    # Try OpenAI-compatible endpoints
    openai_config = settings.get("provider", {}).get("openai", {})
    if openai_config:
        print(f"\nOpenAI Config:")
        print(f"  URL: {openai_config.get('baseUrl') or openai_config.get('endpointUrl')}")
        print(f"  Model: {openai_config.get('model')}")
        print(f"  Key: {'SET' if openai_config.get('apiKey') else 'NOT SET'}")
        
        # Test it
        result = check_openai_connection(
            api_key=openai_config.get("apiKey", ""),
            base_url=openai_config.get("baseUrl", "") or openai_config.get("endpointUrl", ""),
            model=openai_config.get("model", ""),
        )
        print(f"  Result: {result['status']}")
        if result['status'] == 'connected':
            print(f"  Response: {result['response']}")
        else:
            print(f"  Error: {result['error_msg']}")
    
    # Check environment variables
    import os
    env_keys = [k for k in os.environ if 'API_KEY' in k.upper()]
    if env_keys:
        print(f"\nEnvironment API keys: {env_keys}")
    else:
        print("\nNo API keys found in environment variables")
    
    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
