---
name: "autoglm-image-recognition"
description: ">"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\autoglm-image-recognition\\SKILL.md"
---
# AutoGLM Image Recognition Skill

调用 AutoGLM Image Recognition API 对图片进行识别与描述。

---

## 📌 使用前提：获取图片公网 URL

本 skill 要求 `image_url` 必须为**公网可访问的 URL**。请根据图片来源选择对应方式：

| 图片来源 | 处理方式 |
|----------|----------|
| 已有公网 URL（`http://` 或 `https://` 开头） | 直接使用，无需额外处理 |
| 本地文件（用户上传 / 本地路径） | ⚠️ **必须先运行 `upload-mix.py` 上传，获取公网 URL 后再传入** |

> **重要：** 若用户提供的是本地图片（如用户上传的文件、本地磁盘路径等），请勿直接传入文件路径，  
> 必须先执行 **`upload-mix.py`** 完成上传，取得公网 URL 后，再执行图片识别。

---

## Step 1（本地图片）：使用 upload-mix.py 上传获取公网 URL

若图片为本地文件，先运行 `upload-mix.py` 上传：

```bash
python upload-mix.py "<本地图片路径>"
```

**示例：**

```bash
python upload-mix.py "/home/user/photo.jpg"
```

**返回结构：**

```json
{
  "code": 0,
  "msg": "SUCCESS",
  "time": 1773199477734,
  "trace": "78dd001f3ec04c37b6a1d58b5db70fce",
  "data": {
    "message": "",
    "oss_info": [
      {
        "filename": "photo.jpg",
        "oss_name": "auto_fly/xxx/photo.jpg",
        "oss_url": "https://autoglm-agent.aminer.cn/auto_fly/xxx/photo.jpg"
      }
    ]
  }
}
```

从返回结果中提取 `data.oss_info[0].oss_url`，即为后续识别所需的 `image_url`。

---

## Step 2（本地图片完成后）：Image Recognition API

| 项目 | 内容 |
|------|------|
| 地址 | `https://autoglm-api.zhipuai.cn/agentdr/v1/assistant/skills/image-recognition` |
| 方式 | POST |
| 请求体 | 见下方 |

**请求体：**

```json
{
  "prompt": "描述图片",
  "image_url": "https://example.com/image.jpg"
}
```

| 字段 | 说明 | 是否必填 |
|------|------|--------|
| `image_url` | 图片的**公网可访问 URL**。若为本地图片，请先运行 `upload-mix.py` 上传，取 `data.oss_info[0].oss_url` | 必填 |
| `prompt` | 识别指令，如"描述图片"、"识别图中文字"等 | 可选，默认"描述图片" |

**签名 Headers（每次动态生成）：**

- `X-Auth-Appid`: `100003`
- `X-Auth-TimeStamp`: 当前秒级 Unix 时间戳
- `X-Auth-Sign`: MD5(`100003 + "&" + timestamp + "&" + 38d2391985e2369a5fb8227d8e6cd5e5`)

---

## 执行脚本

使用同目录下的 `image-recognition.py`：

```bash
# 仅传图片 URL（使用默认 prompt "描述图片"）
python image-recognition.py "https://example.com/image.jpg"

# 传图片 URL + 自定义 prompt
python image-recognition.py "https://example.com/image.jpg" "识别图中的文字"
```

> ⏱️ **注意：图片识别耗时可能较长**，请耐心等待。  
> 若需设置超时时间，请将 `image-recognition.py` 中的请求调用改为：
> ```python
> with urllib.request.urlopen(req, timeout=300) as resp:
> ```
> 超时时间建议设置为 **300 秒**。

---

## 完整调用流程

```
用户提供本地图片
       ↓
运行 upload-mix.py 上传图片
  python upload-mix.py "<本地图片路径>"
       ↓
从返回结果提取 data.oss_info[0].oss_url 作为 image_url
       ↓
运行 image-recognition.py 进行识别
  python image-recognition.py "<image_url>" ["<prompt>"]
       ↓
将 data.text 结果呈现给用户
```

**用户提供公网 URL 时，直接跳过上传步骤：**

```
用户提供公网图片 URL
       ↓
运行 image-recognition.py 进行识别
  python image-recognition.py "<image_url>" ["<prompt>"]
       ↓
将 data.text 结果呈现给用户
```

---

## 返回结果处理

### 响应结构

```json
{
  "code": 0,
  "msg": "SUCCESS",
  "time": 1773137796961,
  "trace": "298d5fe1efdd4da58ca46d1700d8054b",
  "data": {
    "text": "图片识别结果的详细描述...",
    "tokens": 5588
  }
}
```

### 输出要求

**1. 直接呈现识别结果**  
将 `data.text` 字段的内容直接作为回答呈现给用户，保留原始格式（如 Markdown 加粗等）。