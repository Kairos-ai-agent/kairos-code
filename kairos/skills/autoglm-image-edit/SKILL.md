---
name: "autoglm-image-edit"
description: ">"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\autoglm-image-edit\\SKILL.md"
---
# AutoGLM Image Edit Skill

调用 AutoGLM Image Edit API 对图片进行编辑，并返回编辑后的图片链接。

---

## 使用前提：获取图片公网 URL

本 skill 要求 `image_url` 必须为公网可访问的 URL。请根据图片来源选择对应方式：

| 图片来源 | 处理方式 |
|----------|----------|
| 已有公网 URL（`http://` 或 `https://` 开头） | 直接使用，无需额外处理 |
| 本地文件（用户上传 / 本地路径） | 必须先运行 `upload-mix.py` 上传，获取公网 URL 后再传入 |

若用户提供的是本地图片，请勿直接把本地路径传给图片编辑接口，必须先上传。

---

## Step 1（本地图片）：使用 upload-mix.py 上传获取公网 URL

若图片为本地文件，先运行：

```bash
python upload-mix.py "<本地图片路径>"
```

示例：

```bash
python upload-mix.py "/home/user/photo.jpg"
```

返回结构：

```json
{
  "code": 0,
  "msg": "SUCCESS",
  "data": {
    "oss_info": [
      {
        "oss_url": "https://autoglm-agent.aminer.cn/auto_fly/xxx/photo.jpg"
      }
    ]
  }
}
```

从返回结果中提取 `data.oss_info[0].oss_url`，作为后续图片编辑所需的 `image_url`。

---

## Step 2：Image Edit API

| 项目 | 内容 |
|------|------|
| 地址 | `https://autoglm-api.zhipuai.cn/agentdr/v1/assistant/skills/image-edit` |
| 方式 | POST |
| 请求体 | `{"prompt": "<编辑指令>", "image_url": "<公网图片 URL>"}` |

请求体示例：

```json
{
  "prompt": "添加文字：tom with jerry",
  "image_url": "https://autoglm-agent.aminer.cn/auto_fly/xxx/test.jpg"
}
```

| 字段 | 说明 | 是否必填 |
|------|------|--------|
| `image_url` | 图片的公网可访问 URL。若为本地图片，请先运行 `upload-mix.py` 上传 | 必填 |
| `prompt` | 图片编辑指令，例如“添加文字：tom with jerry”“把背景改成海边日落” | 必填 |

脚本启动时会先向本地服务发起 HTTP GET 请求获取 token：

| 项目 | 内容 |
|------|------|
| 地址 | `http://127.0.0.1:18432/get_token` |
| 方式 | GET |
| 返回 | `Bearer xxx`（直接作为 Authorization 头使用） |

若返回值不含 `Bearer` 前缀，脚本会自动补全。

签名 Headers（每次动态生成）：

- `X-Auth-Appid`: `100003`
- `X-Auth-TimeStamp`: 当前秒级 Unix 时间戳
- `X-Auth-Sign`: MD5(`100003 + "&" + timestamp + "&" + 38d2391985e2369a5fb8227d8e6cd5e5`)

---

## 执行脚本

使用同目录下的 `image-edit.py`：

```bash
# 直接编辑公网图片
python image-edit.py "https://example.com/image.jpg" "添加文字：tom with jerry"

# 先上传本地图片，再编辑
python upload-mix.py "/home/user/photo.jpg"
python image-edit.py "https://autoglm-agent.aminer.cn/auto_fly/xxx/photo.jpg" "把背景改成蓝天草地"
```

---

## 完整调用流程

用户提供本地图片
       ↓
运行 upload-mix.py 上传图片
  python upload-mix.py "<本地图片路径>"
       ↓
从返回结果提取 data.oss_info[0].oss_url 作为 image_url
       ↓
运行 image-edit.py 进行编辑
  python image-edit.py "<image_url>" "<prompt>"
       ↓
将 data.image_url 结果返回给用户，并用 Markdown 图片展示

用户提供公网图片 URL
       ↓
运行 image-edit.py 进行编辑
  python image-edit.py "<image_url>" "<prompt>"
       ↓
将 data.image_url 结果返回给用户，并用 Markdown 图片展示

---

## 返回结果处理

响应结构：

```json
{
  "code": 0,
  "msg": "SUCCESS",
  "time": 1775562648923,
  "trace": "b400f7339a14400b98fab1db41a48c46",
  "data": {
    "image_url": "https://sfile.chatglm.cn/testpath/gen-1775562634145268292_0.jpg"
  }
}
```

输出要求：

1. 从响应中提取 `data.image_url`
2. 以 Markdown 图片格式展示给用户：

```markdown
![编辑后的图片](image_url)
```

3. 若接口返回错误，原样展示错误信息，便于排查
