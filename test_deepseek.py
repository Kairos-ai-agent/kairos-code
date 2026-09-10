import asyncio
import httpx

async def test():
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            'https://api.deepseek.com/v1/models',
            headers={'Authorization': 'Bearer REDACTED'}
        )
        print(f'Status: {r.status_code}')
        print(f'Body: {r.text[:200]}')

asyncio.run(test())
