# 微信官方 ClawBot / iLink 通道（扫码登录 · 多账号）

> **参考腾讯官方 MIT 许可插件 `@tencent-weixin/openclaw-weixin@2.4.9` 重新实现。**
> 本通道是**纯 Python 原生重写**：不依赖 OpenClaw、npm、node、Docker，也不依赖
> 任何第三方服务。只用仓库已有的依赖（`httpx` / `aiosqlite`），协议细节照抄
> 该插件的 TypeScript 源码。

这条通道把**微信个人号**接进 Kairos：在 Kairos 里点一下生成登录二维码 →
用手机微信扫码确认 → Kairos 拿到 bot token，之后用**长轮询**从微信官方 iLink
网关收用户消息，交给 agent 处理，再把回复发回去。**每个「微信账号 + 聊天对象」
映射到一个独立的 Kairos 项目**，彼此不丢信息。

实现文件：

| 文件 | 作用 |
|---|---|
| `kairos/weixin_ilink.py` | 协议核心：`ILinkClient`（7 个端点）、`WeixinLoginSession`、`WeixinAccountStore`、`WeixinChannel` |
| `api/routes/weixin.py` | REST 接口（取码 / 出图 / 轮询 / 账号 / 发送 / 绑定）+ 分发到 agent |
| `web/src/components/WeixinPanel.tsx` | 桌面界面：左下角「微信」入口 + 右侧弹层（出码 / 轮询 / 多账号） |
| `tests/test_weixin_ilink.py` | 离线测试：本地 `http.server` 假网关，30 个用例 |
| `web/src/test/weixinPanel.test.tsx` | 前端弹层测试：出图 / 状态机 / 账号列表，9 个用例 |
| `api/app.py` | lifespan 里建 store / channel 并挂载路由 |

## 一、怎么用（后端接口）

前端只需两步：**取二维码 → 轮询状态**，登录成功后账号自动落库并开始收消息。
二维码本身由**后端直接出 PNG**（`segno` 渲染）：`GET /api/weixin/login/qr.png`
省略 `qrcode` 时就等于「先取一张 + 出图」，一次请求拿到图，并在
`X-Weixin-Qrcode` 响应头里给出轮询要用的 id —— 所以前端不需要任何 QR 库。

```bash
# 1) 取二维码：返回二维码 id + 图片内容字符串
curl -X POST http://127.0.0.1:8000/api/weixin/login/start -H 'Content-Type: application/json' -d '{}'
# → {"qrcode":"2bc5a0be...","qrcode_url":"https://liteapp.weixin.qq.com/q/...","status":"wait","bot_type":"3"}

# 1b) 二维码 PNG（可直接 <img src>，手机能扫）：省略 qrcode 时内部先取一张再出图
curl -D- -o qr.png "http://127.0.0.1:8000/api/weixin/login/qr.png"
# → Content-Type: image/png，X-Weixin-Qrcode: 2bc5a0be...（拿这个 id 去轮询）
#    也可以带 ?qrcode=<id> 用已有会话出图；同一个 id 命中缓存，不重复渲染。
#    图里编码的是 qrcode_url，响应里没有 token。

# 2) 轮询扫码状态（前端每 2~3 秒调一次；服务端每次内部长轮询一次）
curl "http://127.0.0.1:8000/api/weixin/login/status?qrcode=2bc5a0be..."
# → {"qrcode":"2bc5a0be...","status":"wait","connected":false, ...}
#    status 变为 "confirmed" 时，账号已落库、后台轮询已启动；响应里仍然没有 token。
#    若提示需要配对数字，下一次带上 &verify_code=<手机上显示的号码>。

# 3) 列出账号（脱敏：只有 id / user_id / 在线状态，无 token）
curl http://127.0.0.1:8000/api/weixin/accounts

# 4) 用某账号主动发消息（测试用）
curl -X POST http://127.0.0.1:8000/api/weixin/accounts/<id>/send \
  -H 'Content-Type: application/json' -d '{"to":"<对方 user id>","text":"hello"}'

# 5) 删除账号（停后台轮询 + 清 token/游标/绑定）
curl -X DELETE http://127.0.0.1:8000/api/weixin/accounts/<id>

# 6) 查看「账号 + 聊天对象 → 项目」绑定
curl http://127.0.0.1:8000/api/weixin/bindings
```

内置命令（在微信聊天里直接发）：`/status`、`/projects`、`/use <project_id>`、
`/chat <text>`、`/help`。普通文本默认直接和当前项目的 agent 对话。

数据落在 `data/weixin.db`（账号 token、每账号游标、context_token、绑定）。
**token 不硬编码、不进 API 响应、不写日志**：`store.list_accounts()` /
`store.get_account()` 的 SELECT 显式排除 token，只有
`store.get_credentials()` 单独取。

## 二、桌面界面怎么用

通道不需要单独开一个页面，它就在左下角那组里，和语音、机器人、设置并排。

1. 点左下角的 **📱 微信** → 右侧滑出弹层。
2. 点 **生成二维码**：弹层向后端要一次 `GET /api/weixin/login/qr.png`（一次
   请求同时拿到图和在 `X-Weixin-Qrcode` 里的 id），显示一张**手机上真能扫**
   的 PNG；点二维码可以放大。
3. 手机微信扫码：弹层轮询 `GET /api/weixin/login/status?qrcode=<id>`，状态依次
   是 **等待扫码 → 已扫码待确认 → 绑定成功**。轮询是「上一个请求返回后才发下
   一个」（服务端本来就是长轮询），不会把请求堆在一起。
4. 需要配对数字时，弹层出现输入框；填好点「提交并继续」，下一次轮询带上
   `verify_code`。
5. 二维码过期 → 出现「重新生成」按钮；`verify_code_blocked` 等也都有对应提
   示。**任何未识别的 `status` 都显示为「等待中」，绝不白屏。**
6. 弹层下半部分是**已绑定账号**：显示昵称 / 账号 id / 在线状态，可逐个删除
   （`DELETE /api/weixin/accounts/{id}`，同时停掉该账号的后台轮询）。点「再扫
   一个」可以再绑一个账号，多账号互不干扰。

实现落点：`web/src/components/WeixinPanel.tsx`（`WeixinPanel` + `WeixinDrawer`），
接口封装在 `web/src/api/client.ts` 的 weixin 段，入口在
`web/src/components/ChatSidebar.tsx` 的 `SidebarFooter`。所有文案都走 i18n 键
（`web/src/i18n/parts/weixin.json`），组件里没有硬编码中文。

## 三、协议要点

网关根地址 `https://ilinkai.weixin.qq.com/`。每个请求都带这些头：

| 头 | 值 |
|---|---|
| `iLink-App-Id` | `bot`（插件 `package.json` 的 `ilink_appid`） |
| `iLink-App-ClientVersion` | 版本编成 uint32 `0x00MMNNPP`，`2.4.9` → `0x00020409` = `132105` |
| `AuthorizationType` | `ilink_bot_token` |
| `X-WECHAT-UIN` | 随机 uint32 的十进制字符串再做 base64 |
| `Authorization` | `Bearer <token>`（登录后才有；缺失时服务端回 `errcode -14`） |
| `Content-Type` | `application/json` |

七个端点（超时：长轮询 35s / 普通 15s / 轻量 10s）：

| 方法 | 端点 | 说明 |
|---|---|---|
| POST | `ilink/bot/get_bot_qrcode?bot_type=3` | 取二维码；body `{local_token_list:[...]}`，**不需要 token** |
| GET | `ilink/bot/get_qrcode_status?qrcode=<id>[&verify_code=<n>]` | 长轮询扫码状态，**不需要 token** |
| POST | `ilink/bot/getupdates` | 长轮询收消息；body `{get_updates_buf, base_info}` |
| POST | `ilink/bot/sendmessage` | 发消息；body `{msg, base_info}` |
| POST | `ilink/bot/getconfig` | 取 `typing_ticket`（发「正在输入」用） |
| POST | `ilink/bot/sendtyping` | 发「正在输入」（status 1=typing / 2=cancel） |
| POST | `ilink/bot/msg/notifystart` · `notifystop` | 会话开始 / 结束通知 |

每个请求都带 `base_info = {channel_version: "2.4.9", bot_agent: "Kairos"}`。

### 扫码状态取值（`get_qrcode_status` 的 `status`）

| status | 含义 | 本实现处理 |
|---|---|---|
| `wait` | 等待扫码 | 继续轮询 |
| `scaned` | 已扫码，等待确认 | 清除待提交配对码，继续轮询 |
| `need_verifycode` | 需要输入手机上的配对数字 | 返回 `need_verifycode`，前端提示后带 `verify_code` 再轮询 |
| `verify_code_blocked` | 多次输错被锁 | 刷新二维码 |
| `expired` | 二维码过期 | 自动刷新（最多 3 次），重新展示 |
| `scaned_but_redirect` | 需要切换到 `redirect_host` 继续 | 把轮询 base url 切到 `https://<redirect_host>/` |
| `binded_redirect` | 该 bot 已绑定过 | 标记 `already_connected` 并结束 |
| `confirmed` | 已确认 | **捕获 token**，落库并存账号 |

`confirmed` 时的响应字段：`bot_token`（密钥）、`ilink_bot_id`（→ 账号 id）、
`ilink_user_id`（扫码人的 user id）、`baseurl`（该账号后续用的 base url）。

### 消息结构（`getupdates` / `sendmessage`）

`getupdates` 返回 `{ret, msgs, get_updates_buf, longpolling_timeout_ms?}`，
`get_updates_buf` 是**游标**，下次请求原样带回（第一次传 `""`）。`msgs` 每条
是一个 `WeixinMessage`：

| 字段 | 说明 |
|---|---|
| `message_id` | uint64；腾讯可能当裸数字返回，解析时按字符串保留不丢精度 |
| `from_user_id` / `to_user_id` | 发送者 / 接收者 |
| `message_type` | 1=USER，2=BOT（自己的回显，跳过） |
| `message_state` | 0=NEW，1=GENERATING，2=FINISH |
| `item_list` | 内容项数组，文本在 `item_list[i].text_item.text` |
| `context_token` | **回消息时必须原样带回**（按 「账号 + 用户」缓存） |
| `group_id` / `session_id` / `run_id` | 群 / 会话 / 运行标识（本轮未特殊处理群） |
| `create_time_ms` | 毫秒时间戳 |

`item_list[i].type`（`MessageItemType`）：1=TEXT、2=IMAGE、3=VOICE、4=FILE、
5=VIDEO、11=TOOL_CALL_START、12=TOOL_CALL_RESULT。语音若带
`voice_item.text`（语音转文字）直接用文字。

出站 `sendmessage` 的 body 形如：

```json
{
  "msg": {
    "from_user_id": "",
    "to_user_id": "<对方 user id>",
    "client_id": "kairos-weixin-<随机>",
    "message_type": 2,
    "message_state": 2,
    "item_list": [{"type": 1, "text_item": {"text": "回复内容"}}],
    "context_token": "<收消息时下发的>"
  },
  "base_info": {"channel_version": "2.4.9", "bot_agent": "Kairos"}
}
```

响应 `{ret, errmsg, message_id}`；`ret` 非零视为失败。长轮询客户端超时是
**正常控制流**：`getupdates` 返回空（`msgs` 为空、游标不变），`get_qrcode_status`
返回 `{"ret":0,"status":"wait"}`。

## 四、多账号隔离

- **每账号一个后台协程 + 一个 `ILinkClient`**（各自 token），互不干扰；
- **每账号一个游标**，存在 `weixin_sync` 表，一个账号推进不影响另一个；
- **绑定主键是 `(account_id, chat_id)`**：同一个聊天对象在两个账号下各自绑定
  独立项目，不会串（这是照 `kairos/im_accounts.py` 的教训设计的）；
- `context_token` 也按 `(account_id, user_id)` 分开存。

服务重启时，只有**已登录（有 token）且启用**的账号会重新起轮询。

## 五、安全

- token 是密钥。本实现**绝不**把它写进日志、异常信息或任何 API 响应。
- 账号 token 存在 `data/weixin.db`（运行时数据目录，不进仓库）。
- 读取账号一律走脱敏视图（`list_accounts()` / `get_account()` / `to_dict()`）；
  token 只在真正要建客户端 / 落盘时通过 `get_credentials()` 单独取。
- 测试里显式断言：任何 `GET` 响应和本模块日志里都不出现 token 明文。

## 六、已知限制与未验证项

- **只做文本**：图片 / 视频 / 文件 / 语音的 CDN 下载与 AES 解密（微信 CDN 那套）
  **本轮未实现**，预留了扩展位（`MessageItemType` 已枚举，媒体会降级成
  `[图片]` 之类的占位文本）。`getuploadurl` 端点也未封装。
- **配对码流程**：`need_verifycode` 已识别并支持带 `verify_code` 继续轮询，但
  没有做完整的「多次输错 → 刷新 / 锁定」体验打磨。
- **群聊**：`group_id` 已解析但未按群维度分流，仍按发送者会话处理。
- **未完成真实扫码登录**：取二维码端点已对**真实腾讯网关**实测通过
  （`ret=0`，返回 32 位 qrcode 与 `liteapp.weixin.qq.com` 图片链接），
  `GET /api/weixin/login/qr.png` 也已经实测返回真 PNG，并且离线校验过它的
  结构（1 位灰度、37×37 模块、4 模块静默区全白、三个定位图形与定时图形都
  对）。但**扫码之后的 `getupdates` 收发消息没有在真实微信上端到端跑过**
  （需要真实手机扫码）。收发链路的正确性目前由离线 mock 网关测试覆盖；
  桌面弹层也只被 vitest 覆盖，**没有用真手机扫过这张后端出的图**。
