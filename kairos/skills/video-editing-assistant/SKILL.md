---
name: "video-editing-assistant"
description: "专业视频剪辑助手，使用FFmpeg对视频进行智能剪辑处理。支持自动分析视频内容确定剪辑方案，也支持用户自定义剪辑参数（时长、比例、风格、特效）。触发词：剪辑视频、裁剪视频、视频剪切、调整时长、添加转场、视频格式转换、video edit、video trim、video cut"
priority: 0.5
imported-from: "minimax"
source-path: "C:\\Users\\leohu\\.minimax\\skills\\video-editing-assistant\\SKILL.md"
---
# 视频剪辑助手

## Overview

专业的视频剪辑助手，专门负责对下载的视频进行智能剪辑处理。可以自主分析视频内容并决定最佳的剪辑方案，也可以根据用户的具体要求进行精确剪辑。使用 FFmpeg 作为核心处理工具。

## 核心能力

1. **智能剪辑分析**：自动分析视频内容，识别精彩片段、关键场景
2. **多风格剪辑**：支持多种剪辑风格（快节奏、慢镜头、故事叙述等）
3. **时长控制**：精确控制输出视频时长
4. **画面调整**：裁剪、缩放、旋转、添加转场效果

## Workflow

### 1. 视频源获取
1. 默认从 `/workspace/downloads/videos` 目录读取视频
2. 列出目录中可用的视频文件，展示给用户
3. 如果用户指定了其他视频路径，使用用户指定的路径

### 2. 分析与确认
4. 使用 `ffprobe` 获取视频的基本信息（时长、分辨率、编码、帧率等）
5. 确定剪辑模式：
   - **自动模式**（用户未指定具体要求时）：分析视频内容和时长，自动选择合适的剪辑风格，智能提取精彩片段，推荐最佳输出时长
   - **手动模式**（用户有明确要求时）：按用户指定的时间段裁剪、风格处理、画面比例调整、特效或转场
6. 在开始剪辑前，向用户确认剪辑方案

### 3. 创建输出目录
7. 在 `/workspace/downloads/cat/` 下按日期时间创建子文件夹：`/workspace/downloads/cat/YYYYMMDD_HHMMSS/`
   ```bash
   mkdir -p /workspace/downloads/cat/$(date +%Y%m%d_%H%M%S)
   ```

### 4. 执行剪辑
8. 根据确认的方案，使用 FFmpeg 执行剪辑操作（详见下方技术实现部分）
9. 处理过程中向用户提供进度信息

### 5. 结果展示
10. 完成后展示输出文件的位置、文件大小
11. 使用 `ffprobe` 展示输出视频的基本信息（时长、分辨率等）
12. 支持用户对结果进行微调

## 剪辑参数

### 时长选项
| 类型 | 时长 | 适用场景 |
|------|------|----------|
| 短视频 | 15秒 | 抖音、快手 |
| 中等视频 | 30-60秒 | 朋友圈、微博 |
| 长视频 | 1-3分钟 | B站、YouTube |
| 自定义 | 用户指定 | 任意场景 |

### 画面比例
| 比例 | 说明 | 用途 |
|------|------|------|
| 16:9 | 横屏 | 传统视频 |
| 9:16 | 竖屏 | 短视频平台 |
| 1:1 | 方形 | Instagram风格 |
| 原始比例 | 保持不变 | 默认选项 |

### 剪辑风格
- **快节奏**：多切换、动感十足
- **慢镜头**：舒缓、强调细节
- **故事风**：有起承转合
- **纯净风**：简洁、去除冗余

## 技术实现（FFmpeg 命令参考）

### 视频信息获取
```bash
ffprobe -v quiet -print_format json -show_format -show_streams input.mp4
```

### 裁剪（按时间段）
```bash
ffmpeg -i input.mp4 -ss [开始时间] -t [时长] -c copy output.mp4
```

### 缩放（调整分辨率）
```bash
ffmpeg -i input.mp4 -vf scale=[宽]:[高] output.mp4
```

### 画面比例调整
```bash
# 横屏 16:9
ffmpeg -i input.mp4 -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2" output.mp4

# 竖屏 9:16
ffmpeg -i input.mp4 -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2" output.mp4

# 方形 1:1
ffmpeg -i input.mp4 -vf "scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2" output.mp4
```

### 转场效果（淡入淡出）
```bash
# 淡入（前2秒）
ffmpeg -i input.mp4 -vf "fade=t=in:st=0:d=2" -af "afade=t=in:st=0:d=2" output.mp4

# 淡出（最后2秒，需先获取视频总时长）
ffmpeg -i input.mp4 -vf "fade=t=out:st=[总时长-2]:d=2" -af "afade=t=out:st=[总时长-2]:d=2" output.mp4
```

### 多片段合并
```bash
# 1. 先将各片段导出为统一格式的临时文件
ffmpeg -i segment1.mp4 -c copy -f mpegts temp1.ts
ffmpeg -i segment2.mp4 -c copy -f mpegts temp2.ts

# 2. 合并
ffmpeg -i "concat:temp1.ts|temp2.ts" -c copy -bsf:a aac_adtstoasc output.mp4
```

### 或使用 concat 文件方式合并
```bash
# 创建 filelist.txt，内容如下：
# file 'segment1.mp4'
# file 'segment2.mp4'
ffmpeg -f concat -safe 0 -i filelist.txt -c copy output.mp4
```

## 文件与输出规范

### 目录结构
```
/workspace/downloads/videos/       ← 视频源目录（默认）
/workspace/downloads/cat/          ← 剪辑输出根目录
  └── YYYYMMDD_HHMMSS/            ← 按时间戳组织的输出子目录
      ├── edited_video.mp4         ← 剪辑后的视频
      └── ...
```

### 支持的视频格式
- MP4、MOV、AVI、MKV

### 输出质量
- 默认保持与源文件一致的质量
- 使用 `-c copy` 时可无损裁剪（仅限时间裁剪，不涉及滤镜处理）
- 涉及滤镜处理时，使用合适的编码参数保证质量

## 注意事项

- **始终保留原始视频**，剪辑结果单独保存到输出目录
- 处理大文件时注意检查内存和存储空间（可用 `df -h` 检查）
- 优先使用 `-c copy` 进行无损操作（当不需要滤镜处理时）
- 需要重新编码时，推荐使用 H.264 编码：`-c:v libx264 -crf 23 -c:a aac`
- 执行 FFmpeg 命令前，先确认 `ffmpeg` 和 `ffprobe` 已安装可用

## 交互原则

1. **主动沟通**：在开始剪辑前确认用户需求
2. **进度反馈**：处理过程中提供进度信息
3. **结果展示**：完成后清晰展示输出文件位置和基本信息
4. **灵活调整**：支持用户对结果进行微调
