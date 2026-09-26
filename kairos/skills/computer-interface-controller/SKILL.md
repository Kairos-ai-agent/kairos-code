---
name: "computer-interface-controller"
description: "电脑界面自主操控技能。当用户要求操控电脑界面、自动化桌面操作、进行GUI自动化、点击按钮、输入文字、分析屏幕、填写表单、管理文件、控制浏览器时使用。支持本地和远程环境。"
priority: 0.5
imported-from: "minimax"
source-path: "minimax/skills/computer-interface-controller/SKILL.md"
---
# Computer Interface Controller

> **Reality check (Kairos Code).** This skill was imported from MiniMax and its
> "必需工具" (`mcp__…`) is not installed here. The desktop automation that does
> exist is the `computer_use` tool: capture / click / move / type / key / scroll,
> pixel coordinates, no OCR and no element indices. See the `computer-use` skill
> for the real action list, and `browser` for anything on a web page.

全功能电脑界面自主操控技能，支持鼠标、键盘、截图、OCR识别，可操控浏览器、桌面应用、文件系统和表单填写。

## 核心能力

### 1. 鼠标控制
- 绝对/相对位置点击、双击、右键菜单
- 鼠标移动和拖拽操作
- 滚动控制
- 获取当前鼠标位置

### 2. 键盘控制
- 文本输入（支持中英文）
- 快捷键组合（Ctrl+C, Ctrl+V, Alt+Tab等）
- 特殊键操作（Enter, Tab, Esc, Backspace等）
- 键序列执行

### 3. 视觉分析
- 屏幕截图捕获
- 图像识别（定位按钮、图标、文本框）
- OCR文字识别（支持中英文）
- 区域截图和坐标定位

### 4. 应用场景
- **浏览器**: 网页导航、表单填写、数据抓取
- **桌面应用**: 操控任何GUI应用
- **文件操作**: 打开、保存、管理文件和文件夹
- **表单填写**: 自动填充表单字段

## 工作流程

### 基础流程

1. **截图获取**: 捕获当前屏幕或指定区域
2. **视觉分析**: 识别目标元素位置
3. **动作执行**: 执行鼠标点击或键盘输入
4. **结果验证**: 确认操作成功

### 详细流程

```
用户请求 → 分析任务类型 → 截图分析 → 定位目标 → 执行操作 → 验证结果
```

## 实现要求

### 必需工具
- `mcp__matrix__images_understand`: 截图分析和元素识别
- `mcp__matrix__bash`: 执行Python自动化脚本

### Python依赖
```python
pyautogui    # 鼠标键盘控制
Pillow       # 图像处理
pytesseract  # OCR识别
pyscreenshot # 跨平台截图
opencv-python  # 图像识别
```

### 远程控制支持
- SSH远程执行
- VNC协议支持
- 远程桌面连接

## 使用示例

### 示例1: 点击确定按钮
```
用户: "请点击屏幕上的确定按钮"
执行:
1. 截图获取当前屏幕
2. 使用images_understand识别"确定"按钮位置
3. 使用pyautogui.click()点击该位置
```

### 示例2: 填写表单
```
用户: "在登录页面填写用户名admin密码123456并点击登录"
执行:
1. 截图确认当前页面状态
2. OCR识别用户名输入框并点击
3. 使用pyautogui.typewrite("admin")
4. 重复密码输入和登录按钮点击
```

### 示例3: 自动化浏览器操作
```
用户: "打开Chrome访问google.com"
执行:
1. pyautogui.press('win')
2. typewrite('Chrome')
3. press('enter')
4. 执行后续URL访问操作
```

## 安全建议

1. **确认机制**: 关键操作前请求用户确认
2. **权限控制**: 限制敏感操作权限
3. **操作日志**: 记录所有自动化操作
4. **异常处理**: 操作失败时自动停止并报告

## 错误处理

| 错误类型 | 处理方式 |
|---------|---------|
| 元素未找到 | 重试3次，扩大搜索范围 |
| 操作超时 | 等待后重试，提示用户 |
| 权限不足 | 请求提升权限或用户代操作 |
| 远程连接失败 | 检查网络，尝试重连 |

## 参考文档

详细实现指南见 [references/implementation.md](references/implementation.md)
