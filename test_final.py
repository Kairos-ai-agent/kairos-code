"""Test actual LLM connectivity via Python SDK."""
import asyncio
from kairos.llm.providers.openai_provider import OpenAIProvider
from kairos.llm.base import LLMConfig, LLMMessage

async def test():
    # Test Agnes AI
    config = LLMConfig(
        provider='openai',
        api_key='REDACTED',
        base_url='https://apihub.agnes-ai.com/v1',
        model='agnes-3.0-flash',
        max_tokens=50,
    )
    p = OpenAIProvider(config)
    
    try:
        r = await p.complete([LLMMessage(role='user', content='Say hello in one word')])
        print(f'Agnes AI Connection: SUCCESS')
        print(f'  Model: {r.model}')
        print(f'  Response: {r.content}')
        print(f'  Tokens: {r.usage}')
    except Exception as e:
        print(f'Agnes AI Connection: FAILED - {type(e).__name__}: {e}')

asyncio.run(test())
