import requests
import json

# 测试后端 /api/config/models/custom/fetch 端点
url = "http://localhost:8847/api/config/models/custom/fetch"
payload = {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "REDACTED",
    "protocol": "openai"
}

try:
    resp = requests.post(url, json=payload, timeout=15)
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text[:500]}")
except requests.exceptions.ConnectionError as e:
    print(f"Connection Error: Backend not running? {e}")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
