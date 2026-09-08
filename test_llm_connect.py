import asyncio
from kairos.llm.base import LLMConfig, LLMMessage
from kairos.llm.providers.openai_provider import OpenAIProvider

async def test_connection():
    """Test LLM connection with actual API key."""
    config = LLMConfig(
        provider='openai',
        api_key='REDACTED',
        base_url='https://apihub.agnes-ai.com/v1',
        model='agnes-2.5-flash',
        max_tokens=100,
        temperature=0.7,
    )
    
    provider = OpenAIProvider(config)
    messages = [LLMMessage(role='user', content='Say hello in one word')]
    
    try:
        result = await provider.complete(messages)
        print(f"✅ Connection successful!")
        print(f"   Response: {result.content}")
        print(f"   Model: {result.model}")
        print(f"   Usage: {result.usage}")
        return True
    except Exception as e:
        print(f"❌ Connection failed: {type(e).__name__}: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(test_connection())
    exit(0 if success else 1)
