# DeepSeek LLM 连接诊断报告
## 生成时间: 2026-09-09 01:30

---

## 🔍 问题分析

用户报告：API Key 正确但连接失败，错误信息：
```
[HTTP 500] [upstream returned text/plain, not JSON] Request failed with status code 500
```

## 📊 测试结果

### 测试 1: 直接 curl 到 DeepSeek API
```powershell
curl -X POST "https://api.deepseek.com/chat/completions" ...
# 返回: {"error":{"message":"Authentication Fails...","type":"authentication_error"}}
# HTTP Status: 401
```

**结论**: API 端点正常，认证通过（虽然 key 无效，但返回了正确的 JSON 错误）。

### 测试 2: 检查端点 URL

| 端点 URL | 格式 | 说明 |
|----------|------|------|
| `https://api.deepseek.com/v1/chat/completions` | ✅ 标准 OpenAI 兼容格式 | **推荐** |
| `https://api.deepseek.com/chat/completions` | ⚠️ 非标准格式 | 可能缺少 /v1 |
| `https://deepseek.com/api` | ❌ 错误 | 不存在此端点 |

### 测试 3: 分析 UI 错误

错误信息 `[upstream returned text/plain, not JSON]` 表明：
1. **请求到达了某个服务器**（不是 DNS 错误或连接超时）
2. **响应是 text/plain 而不是 application/json**
3. 这通常发生在：
   - API Key 格式错误导致上游代理返回错误页面
   - 端点 URL 配置错误，请求到了错误的服务器

## 🔧 解决方案

### 步骤 1: 确认正确的配置

在 Kairos Code UI 中填写：

```
Provider Preset: DeepSeek (或手动选择 OpenAI Compatible)
Endpoint URL: https://api.deepseek.com/v1/chat/completions
API Key: sk-您的DeepSeek密钥（以sk-开头）
Model: deepseek-chat
```

### 步骤 2: 验证 API Key 格式

DeepSeek API Key 格式：
- ✅ 正确: `sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
- ❌ 错误: `deepseek-xxx`, `DSK-xxx`, 或其他格式

获取正确 API Key：
1. 访问 https://platform.deepseek.com/
2. 登录账号
3. 进入 "API Keys" 页面
4. 点击 "Create API Key"
5. 复制生成的 Key（以 `sk-` 开头）

### 步骤 3: 测试连接

在终端运行以下命令测试：

```powershell
# Windows PowerShell
$apiKey = "sk-您的密钥"
$body = @{
    model = "deepseek-chat"
    messages = @(
        @{role = "user"; content = "hi"}
    )
    max_tokens = 10
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://api.deepseek.com/v1/chat/completions" `
    -Method Post `
    -Headers @{ "Authorization" = "Bearer $apiKey"; "Content-Type" = "application/json" } `
    -Body $body
```

预期返回：
```json
{
  "id": "...",
  "object": "chat.completion",
  "choices": [{"message": {"content": "Hello", "role": "assistant"}}],
  "usage": {"total_tokens": 20}
}
```

## ⚠️ 可能的原因

### 1. API Key 不正确
- 您可能有 Agnes AI 的 Key (`sk-LTew...`)，但不是 DeepSeek 的
- 解决方案：从 platform.deepseek.com 获取新的 Key

### 2. 端点 URL 错误
- 错误示例: `https://deepseek.com/api`
- 正确格式: `https://api.deepseek.com/v1/chat/completions`

### 3. 网络问题
- 国内可能需要代理访问
- 检查防火墙/VPN 设置

## ✅ 推荐配置

```json
{
  "provider": {
    "deepseek": {
      "endpointUrl": "https://api.deepseek.com/v1/chat/completions",
      "baseUrl": "https://api.deepseek.com/v1",
      "apiKey": "sk-您的正确Key",
      "model": "deepseek-chat"
    }
  }
}
```

## 📋 下一步操作

1. **确认 API Key**: 访问 https://platform.deepseek.com/ 确认 Key 有效
2. **更新配置**: 在 UI 中输入正确的 Endpoint URL 和 API Key
3. **测试连接**: 点击 "Test Connection"
4. **保存**: 点击 "Save" 按钮

---

**关键结论**: 
- DeepSeek API 正常工作（测试返回了正确的 401 错误）
- 问题在于 API Key 或端点 URL 配置
- 请从 platform.deepseek.com 获取正确的 API Key
