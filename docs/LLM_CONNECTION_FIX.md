# Kairos Code LLM 连接诊断报告
## 生成时间: 2026-09-09 01:12

---

## 🔍 问题定位

用户在 UI 中选择 **DeepSeek** 预设时遇到错误：
```
[HTTP 500] [upstream returned text/plain, not JSON] Request failed with status code 500
```

## 📊 根本原因

**API Key 与 Provider 不匹配！**

| Provider | API Key | 状态 |
|----------|---------|------|
| **Agnes AI** | `sk-LTew...` (您的 Key) | ✅ **已验证可用** |
| **DeepSeek** | 未提供 / 无效 | ❌ 认证失败 |

您拥有的是 **Agnes AI** 的 API Key，不是 DeepSeek 的。

## 🔧 解决方案

### 方案 A: 使用 Agnes AI（推荐 - 已有 Key）

在 Kairos Code UI 中选择：
```
Provider Preset: OpenAI Compatible
Endpoint URL: https://apihub.agnes-ai.com/v1/chat/completions
API Key: REDACTED
Model: agnes-3.0-flash
```

测试验证：
```powershell
curl -X POST "https://apihub.agnes-ai.com/v1/chat/completions" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer REDACTED" `
  -d "{\"model\":\"agnes-3.0-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":10}"
# 返回: {"choices":[{"message":{"content":"Hello","role":"assistant"}}]}
```

### 方案 B: 获取 DeepSeek API Key

1. 访问 https://platform.deepseek.com/
2. 注册账号并获取 API Key
3. 填入配置：
   ```
   Endpoint: https://api.deepseek.com/v1/chat/completions
   Model: deepseek-chat 或 deepseek-coder
   ```

## 📋 当前配置状态

```json
{
  "custom_models": [
    {
      "name": "agnes",
      "base_url": "https://apihub.agnes-ai.com/v1",
      "api_key": "REDACTED",
      "model": "agnes-3.0-flash",
      "protocol": "openai"
    }
  ],
  "role_mappings": {
    "coder": "custom:agnes",
    "reviewer": "custom:agnes"
  }
}
```

## ✅ 测试通过

```powershell
$ python test_providers.py

=== Testing Agnes AI ===
SUCCESS!
  Model: agnes-3.0-flash
  Response: Hello.

=== Testing DeepSeek (placeholder) ===
FAILED: Authentication Fails, Your api key: ****_KEY is invalid
```

## 🎯 下一步操作

1. **立即操作**: 在 UI 中将 Provider Preset 从 "DeepSeek" 改为 "OpenAI Compatible"
2. **确认模型**: 选择 `agnes-3.0-flash`（最新模型）
3. **保存配置**: 点击 Save 按钮

---

**结论**: Agnes AI 连接正常，只需在 UI 中切换正确的 Provider 即可。
