import requests
import json

# 更新 DeepSeek 配置
update_data = {
    "provider": {
        "active": "openai",
        "openai": {
            "endpointUrl": "https://api.deepseek.com/v1/chat/completions",
            "baseUrl": "https://api.deepseek.com/v1",
            "apiKey": "REDACTED",
            "model": "deepseek-v4-flash"
        }
    },
    "custom_models": [
        {
            "name": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "REDACTED",
            "model": "deepseek-v4-flash",
            "protocol": "openai"
        }
    ],
    "role_mappings": {
        "coder": "custom:deepseek",
        "reviewer": "custom:deepseek"
    }
}

try:
    resp = requests.post(
        "http://localhost:8847/api/projects/settings",
        json=update_data
    )
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")
except Exception as e:
    print(f"Error: {e}")
