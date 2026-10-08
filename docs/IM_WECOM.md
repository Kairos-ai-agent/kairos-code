# 企业微信「自建应用」接入（双向对话）

Kairos 支持把**企业微信自建应用**接成一条双向通道：成员在企业微信 App /
PC 客户端里给应用发消息，消息经回调进入 Kairos、交给 agent 处理，结果再
加密回包给该成员。这条通道与「群机器人 webhook」不同 —— 群机器人只能往
固定群里单向推送，自建应用才能识别**是谁在跟你说话**并单独回复。

> 实现文件：`kairos/wecom.py`（`WeComBot` / `WeComEventForwarder` /
> `WeComBindingStore`）、`api/routes/wecom.py`（回调与配置路由）、
> 配置字段在 `kairos/settings_store.py`（`WeComSettings`）。

> **未经真机验证（重要）。** 这条通道目前**只在离线测试里跑过**：测试用
> FastAPI 的 `TestClient` 直接打回调路由、用假对象替换 orchestrator，
> **不连企业微信、也不依赖真实账号**（见 `tests/test_wecom.py`）。也就是说，
> 验签、AES 解密 / 加密、命令解析、以及「一条消息确实走到 agent 回复路径」
> 都有用例保证（含一份独立构造的已知答案向量）；但**没有在真实企业微信里
> 端到端收发过消息** —— 真实回调 URL 验证、真实成员、真实的 5 秒响应窗口
> 都未联调过。请按「已实现、离线测试通过、待真机联调」理解这一侧能力。

个人也能免费注册一个企业微信来试用；下面每个值都能在管理后台找到。

## 一、准备：建一个自建应用，拿到 5 个值

登录 <https://work.weixin.qq.com>（管理后台），按顺序取这 5 个值：

| # | 值 | 在哪里拿 | 说明 |
|---|---|---|---|
| 1 | `corp_id` | **我的企业 → 企业信息** 页面底部「企业 ID」 | 形如 `ww1234567890abcdef`，整个企业唯一 |
| 2 | `corp_secret` | **应用管理 → 应用 → 自建 → 你的应用 → Secret**，点「查看」 | 每个应用一个；泄露等于别人能冒充你的应用发消息 |
| 3 | `agent_id` | 同一个应用详情页的 **AgentId** | 一个数字，主动发消息时要带上它 |
| 4 | `token` | **应用详情 → 接收消息 → 设置 API 接收** 里的 **Token** | 点「随机获取」生成；用于回调验签 |
| 5 | `encoding_aes_key` | 同一处的 **EncodingAESKey** | 点「随机获取」生成，**固定 43 个字符**；用于回调加解密 |

第 4、5 项在「设置 API 接收」弹窗里和回调 URL 一起配置，见下一节。

## 二、回调地址填什么

在 **应用详情 → 接收消息 → 设置 API 接收** 弹窗里：

- **URL**：填 Kairos 的对外地址 + `/api/wecom/webhook`
  - 例：`https://your-domain.example.com/api/wecom/webhook`
  - 企业微信服务器必须能访问到这个地址。本机自测可用内网穿透工具
    把它映射到一个公网地址；生产环境请用 HTTPS 域名。
  - 保存弹窗时企业微信会先对该地址发一次 **GET 验证请求**（带
    `msg_signature` / `timestamp` / `nonce` / `echostr`）。Kairos 的
    `/api/wecom/webhook` 会自动完成验签、AES 解密并原样返回明文
    `echostr`，所以保存应该直接通过。
- **Token**：即上面的第 4 项，两边保持一致。
- **EncodingAESKey**：即上面的第 5 项。
- **消息加解密方式**：选「安全模式」（对应本实现；企业微信的明文/兼容
  模式不在本实现的范围内）。

> 提示：回调 URL 只有在 Kairos 后台已经**填好这 5 个值并保存**之后才能
> 通过验证，所以建议先在 Kairos 侧录入（第三节），再回后台点保存。

## 三、在 Kairos 侧录入这 5 个值

通过配置接口写入（也可由前端设置面板调用）：

```bash
curl -X PUT http://127.0.0.1:8000/api/wecom/config \
  -H 'Content-Type: application/json' \
  -d '{
        "corp_id": "ww1234567890abcdef",
        "corp_secret": "<应用 Secret>",
        "agent_id": "1000002",
        "token": "<回调 Token>",
        "encoding_aes_key": "<43 字符 EncodingAESKey>",
        "enabled": true
      }'
```

- 配置保存在 `data/settings.json` 的 `wecom` 段，**不硬编码**。
- 读取配置 `GET /api/wecom/config` 只返回脱敏信息（`has_corp_secret` /
  `has_token` / `has_encoding_aes_key` 布尔位），**绝不回显任何密钥明文**。
- `corp_secret` / `token` / `encoding_aes_key` 留空提交表示「保持原值」，
  所以前端拿到脱敏结果后再保存不会把密钥清空。
- `enabled` 只控制「Kairos 主动推送事件」这条出站方向；回调入站是否
  放行由签名校验决定。

## 四、验证与使用

1. 在企业管理后台保存回调 URL —— 应提示保存成功（说明 URL 验证通过）。
2. 用企业微信客户端给这个应用发一条消息（例如 `你好`）。
3. agent 的回复会加密回包给该成员；同一个成员的消息会绑定到同一个
   Kairos 项目（首次发消息时自动创建，绑定记录在 `data/wecom.db`）。
4. 内置命令（在聊天里直接发）：
   - `/status` —— 当前绑定的项目
   - `/projects` —— 列出项目
   - `/use <project_id>` —— 把当前会话绑定到指定项目
   - `/chat <text>` —— 显式走 agent（等价于直接发文本）
   - `/help` —— 命令帮助

诊断用接口：

```bash
# 主动给某个成员发测试消息（members 的 UserID 可在通讯录里看到）
curl -X POST http://127.0.0.1:8000/api/wecom/test \
  -H 'Content-Type: application/json' \
  -d '{"userid": "zhangsan", "text": "hello from Kairos"}'

# 查看成员 → 项目 的绑定
curl http://127.0.0.1:8000/api/wecom/bindings
```

## 五、技术要点与已知限制

- **access_token 有缓存**：自建应用调接口前要先用 `corp_id` +
  `corp_secret` 换 `access_token`（有效期 7200 秒）。换取的接口有频率
  限制，所以 `WeComBot` 在进程内缓存 token，只在过期前（留 300 秒余量）
  才重新请求。
- **5 秒响应窗口**：企业微信要求回调 5 秒内响应，否则会重试三次。当前
  实现是**同步回复**（等 agent 出结果后加密回包），如果 agent 处理较慢，
  可能触发企业微信重试。较长的分析任务更适合用主动推送（`WeComEventForwarder`
  订阅 agent 事件后调 `WeComBot.send_text` 推结果）。
- **加解密依赖**：AES-256-CBC 需要 `pycryptodome` 或 `cryptography` 之一
  （任一即可，模块内 lazy 导入）。两者都没有时，收发回调会报错并提示安装。
- **只处理文本消息**：图片、事件等其它类型目前静默确认（不回包）。
- **加解密细节**（实现遵循企业微信文档）：`EncodingAESKey` 补一个 `=`
  后 base64 解码得到 32 字节 key，IV 取 key 前 16 字节；明文结构为
  `random(16B) + msg_len(4B 大端) + msg + receiveid`；AES-256-CBC +
  PKCS7（块大小 32）；签名 = token/timestamp/nonce/encrypt 排序拼接后的
  SHA1。
