"""Test LLM connections."""
import asyncio
from kairos.llm.providers.openai_provider import OpenAIProvider
from kairos.llm.base import LLMConfig, LLMMessage

async def test_provider(name, api_key, base_url, model):
    """Test a single provider."""
    print(f"\n=== Testing {name} ===")
    config = LLMConfig(
        provider='openai',
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_tokens=20,
    )
    p = OpenAIProvider(config)
    try:
        r = await p.complete([LLMMessage(role='user', content='Say hi')])
        print(f"SUCCESS!")
        print(f"  Model: {r.model}")
        print(f"  Response: {r.content}")
        return True
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        return False

async def main():
    # Test Agnes AI (confirmed working from earlier curl)
    await test_provider(
        "Agnes AI",
        api_key="REDACTED",
        base_url="https://apihub.agnes-ai.com/v1",
        model="agnes-3.0-flash"
    )
    
    # Test DeepSeek with placeholder key
    await test_provider(
        "DeepSeek (placeholder)",
        api_key="sk-test-key-does-not-exist",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat"
    )

if __name__ == "__main__":
    asyncio.run(main())
