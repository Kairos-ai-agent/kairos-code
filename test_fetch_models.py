"""Direct test of the models fetch logic."""
import asyncio
import httpx

async def test_fetch_models():
    """Simulate what the backend does."""
    base = "https://api.deepseek.com/v1"
    api_key = "REDACTED"
    
    url = f"{base}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
            resp = await client.get(url, headers=headers)
            print(f"Status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                models = [
                    {"id": m["id"], "name": m.get("id", "")}
                    for m in data.get("data", [])
                ]
                models.sort(key=lambda x: x["id"])
                print(f"Success! Found {len(models)} models:")
                for m in models:
                    print(f"  - {m['id']}")
            else:
                print(f"Error: {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        print(f"Exception: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(test_fetch_models())
