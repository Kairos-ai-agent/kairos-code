---
name: "autoglm-asr-translate"
description: ">"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/autoglm-asr-translate/SKILL.md"
---
# AutoGLM ASR Translate Skill

将本地音频文件调用 AutoGLM ASR API 转录为文字。  
**所有音频**（无论大小、时长）统一先通过 ffmpeg 切分为 ≤25s 的 WAV 分块，再并发转录、顺序合并。

---

## 📌 前置依赖

| 依赖 | 用途 | 安装方式 |
|------|------|----------|
| Python 3 | 运行脚本 | 系统自带 |
| ffmpeg | 音频解码 + 分块（必需） | `brew install ffmpeg`（macOS）/ `apt install ffmpeg`（Linux） |

> **注意：** 不再依赖 pydub，仅需 ffmpeg 即可运行。

---

## API 信息

| 项目 | 内容 |
|------|------|
| 地址 | `https://autoglm-api.zhipuai.cn/agentdr/v1/assistant/skills/asr-translate` |
| 方式 | POST（multipart/form-data） |
| 字段 | `file`：音频文件二进制 |

**签名 Headers（每次动态生成）：**

- `X-Auth-Appid`: `100003`
- `X-Auth-TimeStamp`: 当前秒级 Unix 时间戳
- `X-Auth-Sign`: MD5(`100003 + "&" + timestamp + "&" + 38d2391985e2369a5fb8227d8e6cd5e5`)

**响应结构：**

```json
{
  "code": 0,
  "msg": "SUCCESS",
  "time": 1774333123073,
  "trace": "d69a3d58b39b472d815fe8556f22b7a5",
  "data": {
    "text": "转录结果文本..."
  }
}
```

---

## 执行脚本

```bash
python asr-translate.py "<音频文件路径>"
```

**示例：**

```bash
python asr-translate.py "/Users/shibin/Desktop/recording.wav"
```

---

## 完整调用流程

```
用户提供音频文件路径
       ↓
检查 ffmpeg 是否可用（不可用则报错退出）
       ↓
脚本自动获取 Token（本地服务 :18432）
       ↓
ffprobe 探测音频时长
       ↓
ffmpeg 将音频转为单声道 16kHz WAV，按 25s 切块
  └── 无法探测时长 → 整段转换为单块
       ↓
分块数 == 1 → 直接转录
分块数 >  1 → 并发转录（并发 5）→ 顺序合并
       ↓
打印转录结果，清理临时目录
```

---

## 处理逻辑说明

### ffmpeg 分块

- 使用 `ffprobe` 探测总时长，计算需要的分块数
- 使用 `ffmpeg -ss <start> -t 25` 精确切片，同时转为**单声道 16kHz WAV**（降低 API 报错概率）
- 分块文件写入系统临时目录（ASCII 路径，避免中文路径问题），转录完成后自动清理

### 并发转录

- 使用 `ThreadPoolExecutor` 并发发送各分块请求，固定并发数 5
- 每块转录完成后实时打印进度 `[已完成/总数]`
- 转录失败的分块以空字符串填充，不中断整体流程

### 结果合并

- 按分块原始顺序拼接文本（`chunk_0000 → chunk_0001 → ...`）

---

## 支持的音频格式

`mp3`, `wav`, `m4a`, `flac`, `ogg`, `webm`（需 ffmpeg 支持对应格式解码）

---

## 常见错误

| 错误信息 | 原因 | 解决方法 |
|----------|------|----------|
| `未找到 ffmpeg` | ffmpeg 未安装或不在 PATH | `brew install ffmpeg` / `apt install ffmpeg` |
| `无法从本地服务获取 token` | 本地 Token 服务未启动 | 启动 `:18432` 本地服务 |
| `文件不存在` | 路径错误 | 使用绝对路径 |
| `API 错误 code=...` | Token 失效或请求异常 | 检查 Token 有效性，重试 |

---

## 输出要求

将 `data.text`（或合并后全文）直接呈现给用户，保留原始断句与标点格式。