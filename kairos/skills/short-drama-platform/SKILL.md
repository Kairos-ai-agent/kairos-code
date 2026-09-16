---
name: "short-drama-platform"
description: "Build collaborative short drama sharing platforms — script/image/video sharing with community voting. Use when creating websites for creative collaboration on short dramas, scripts, or video content w"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\.archive\\short-drama-platform\\SKILL.md"
---
# 短剧共创平台 · Short Drama Co-Creation Platform

## 触发条件
当用户需要构建一个**短剧共创/分享平台网站**时激活。典型场景：
- "建一个共享剧本的网站"
- "做个可以投票选剧本的平台"
- "短剧素材共享站"
- 从评测网站转型为创作者社区网站

## 核心功能模块

### 1. 剧本共享 (Script Sharing)
- 上传剧本（文本/文件两种格式）
- 剧本列表浏览（按题材分类：甜宠、逆袭、战神、穿越、悬疑等）
- 剧本详情页（完整内容 + 元数据：类型、集数、标签）
- 搜索/筛选功能

### 2. 素材共享 (Asset Sharing)
- 图像素材：角色参考图、场景概念图、分镜草图
- 视频素材：样片、参考视频、片段
- 与剧本关联：每个剧本可挂载多个素材

### 3. 投票/社区 (Voting & Community)
- 点赞/评分机制
- 投票选剧本（社区决定哪些剧本值得拍摄）
- 评论功能（可选）

### 4. 用户系统 (User System)
- 创作者身份
- 上传管理
- 个人作品集

## 技术架构决策

### 阶段一：原型/MVP（纯前端）
```
project/
├── index.html          # 主入口
├── css/
│   └── style.css       # 全局样式
├── js/
│   ├── app.js          # 路由 + 状态管理
│   ├── scripts.js      # 剧本模块
│   ├── assets.js       # 素材模块
│   └── voting.js       # 投票模块
├── data/               # 示例数据（JSON）
└── assets/             # 静态资源
```
- IndexedDB 存储用户数据
- localStorage 存储偏好
- 单文件或多文件均可

### 阶段二：接入后端
- Supabase / Firebase 替代本地存储
- Cloudflare Worker 部署（沿用用户习惯）
- 真实用户认证

## 设计要点

### 用户偏好（来自历史反馈）
- **排版极度敏感**：文字不重叠、间距充足、专业级输出
- **中英双语**：界面支持中英文切换
- **深色主题**：创意类平台偏好暗色 UI
- **响应式**：移动端优先（短剧主要在手机上消费）

### 题材分类（短剧常用）
```
甜宠 Romance | 逆袭 Counterattack | 战神 War God
穿越 Time Travel | 重生 Rebirth | 悬疑 Suspense
霸总 CEO | 萌宝 Baby | 玄幻 Fantasy | 都市 Urban
```

### 剧本文件格式建议
- 纯文本：`.txt` / `.md`（直接展示）
- 结构化：JSON（包含场景、角色、台词分离）
- 富文本：HTML（保留格式）

## 与已有技能的关联
- `short-drama-scriptwriting` → 剧本创作方法论（参考但不混用）
- `ai-storyboard-artist` → 分镜生成
- `comfyui` → 关键帧图像生成
- `seedance-prompt` / `seedance2-prompt` → 视频生成提示词
- `kairos-canvas` → SPA 开发经验（但本项目是不同用途）

## API 登录错误处理（常见陷阱）

当使用 Cloudflare D1 后端时，`store.authenticateMember()` 必须用 try/catch 包裹 `_api()` 调用，因为 API 返回非 200 状态码（如 401 密码错误）时 `_api()` 会 throw Error。如果 throw 没有被 catch，`login.js` 的 `submit` 函数会静默崩溃——不显示 toast、页面无反应。

```javascript
// ❌ 错误写法 — _api 401 throw → 静默崩溃
async authenticateMember(username, password) {
  const result = await _api('POST', '/members/login', { username, password });
  if (result.success) {
    await this.loginWithToken(result.token, result.member);
    return result.member;
  }
  return null;
}

// ✅ 正确写法 — catch 所有异常，返回 null
async authenticateMember(username, password) {
  try {
    const result = await _api('POST', '/members/login', { username, password });
    if (result.success) {
      await this.loginWithToken(result.token, result.member);
      return result.member;
    }
    return null;
  } catch (e) {
    return null;
  }
}
```

`login.js` 的 `submit` 函数通过 `if (member)` 判断，收到 null 时显示"用户名或密码错误"toast。

## 脚本加载顺序 (Critical)
所有 <script> 标签必须按依赖顺序加载：
```
i18n.js → store.js → router.js → app.js → views/*.js
```
- **app.js 必须在 views 之前加载**，因为它定义了 `updateUserMenu()`、`doLogout()`、`toggleDropdown()` 等全局函数
- 如果 app.js 加载在 login.js 等视图之后，`onclick="doLogout()"` 会因函数未定义而静默失效（点击无反应）
- 缓存版本号（`?v=3`）用于强制浏览器刷新，但实际 Worker 单个 bundle 不受此限制

## 管理员硬编码模式
```javascript
isAdmin() {
    return this.currentUser && (
        this.currentUser.isAdmin ||
        this.currentUser.username === 'Kairos' ||
        this.currentUser.username === 'echo-7s' ||
        this.currentUser.username === 'Kairos_admin'
    );
}
```
- 硬编码用户名列表在 `store.js` 的 `isAdmin()` 中维护
- 配合注册流程中的 master code 检查（`SDF-2026-MASTER-A7K9X2` 设置 `isAdmin: true`）
- `updateUserMenu()` 在每次导航和登录/登出后调用，控制 `.nav-members-link` 和下拉菜单的显示

## 参考文件
- `references/indexeddb-patterns.md` — IndexedDB 版本升级、CRUD 模板、ID 生成模式

## 部署
- Cloudflare Worker（用户已有部署经验）
- 静态托管即可，无需服务器端渲染
- 域名：沿用自己域名或新购

### 部署流程
1. 修改 JS/CSS/HTML 源文件
2. 语法检查：`node --check js/store.js && node --check js/app.js && node --check js/views/*.js`
3. 构建 + 部署：`python3 deploy.py`（读取 `deploy_cf.py` 构建 + 从 `_secrets.json` 读取配置 + curl 上传）
4. 必须保留的文件：`deploy_cf.py`（构建器）、`deploy.py`（部署器）、`_secrets.json`（配置）、`.gitignore`（排除敏感文件）
5. CF API Token 从环境变量 `CF_API_TOKEN` 读取，或使用 base64 内嵌回退
6. 验证：硬刷新 Ctrl+Shift+R 确保新 JS 加载

## 新增功能模块

### 5. 剧集认领 (Episode Claiming, Max 2)\n- 同一集允许最多 2 人认领（非唯一约束）\n- DB 版本升级时删除旧 unique 索引（`['scriptId', 'episodeNum']`）\n- 前端检查：`epClaims.length >= 2` 阻止第三次认领\n- 同一成员不能重复认领同一集\n- 错误类型：`episode_already_claimed` / `episode_max_claims`\n- `renderEpisodes` 按 `epGroup` 分组展示认领者\n- **认领后剧本显示**：会员认领某集后只能看到自己认领的剧集内容（`myClaim.episodeContent`），**不可看完整剧本**。完整剧本仅管理员可见。认领后通过「编辑」按钮用 `prompt()` 自行填写剧集内容。

### 6. 素材分类 (Asset Categories)
- 上传素材时指定分类：角色(character)、场景(scene)、道具(prop)、角色音频(char_audio)
- 分类标签带颜色：`catColors = { character: '#6c5ce7', scene: '#00b894', prop: '#fdcb6e', char_audio: '#e17055' }`
- 资产列表支持按分类筛选（全部/角色/场景/道具/角色音频）
- 现有素材（无 category 字段）向后兼容显示在"全部"中
- i18n 常量 `ASSET_CATEGORIES` 在 `i18n.js` 中定义

### 7. 管理员管理 (Admin Features)
- 硬编码管理员 `Kairos`（`isAdmin()` 检查 `username === 'Kairos'`）
- 详情页：管理员可删除单个剧本（右侧信息卡红色按钮）
- 首页/浏览页：管理员可删除剧本（卡片底部 ✕ 按钮，`event.stopPropagation()` 阻止冒泡）
- 删除时同时清理关联素材

### 8. 提示词库 (Prompt Library)
- 独立 IndexedDB 存储 `prompts`（DB v4），字段：`id, scriptId, name, category, content, createdBy, createdByName, createdAt`
- 管理员：上传提示词（名称+分类+内容）、删除提示词
- 普通会员：📋 复制提示词（`navigator.clipboard.writeText`，降级 `document.execCommand('copy')`）
- 分类标签与素材共享 `ASSET_CATEGORIES` 颜色

### 9. IndexedDB 版本管理
- 版本号从 v1 递进，每次新增 store 或修改索引时 +1
- `onupgradeneeded` 处理新增 store 和旧版本迁移
- 删除索引：`if (store.indexNames.contains('unique')) store.deleteIndex('unique')`
- 当前 DB 版本：`ShortDramaForgeDB` v4

### 10. 管理员会员管理 (Admin Member Management)
- 路由 `/admin/members`，视图 `views.adminMembers`（定义在 admin.js 中）
- 入口：右上角下拉菜单（`updateUserMenu()` 中动态添加）和顶栏导航 `.nav-members-link`
- `index.html` 中静态添加 `<a href="#/admin/members" class="nav-link nav-members-link">`，CSS `.nav-members-link { display: none; }` 默认隐藏
- `app.js` 的 `updateUserMenu()` 中根据 `store.isAdmin()` 显示/隐藏：`membersLink.style.display = store.isAdmin() ? '' : 'none'`
- 页面功能：会员列表卡片（头像、显示名、用户名、邮箱、注册时间、Admin 标签）、认领详情（按剧本分组显示认领集数）
- 注册路由：`app.js` 的 `init()` 中 `registerView('/admin/members', views.adminMembers.render)`
- 需要 `store.isAdmin()` 守卫，非管理员重定向到 `/login`

### 11. 用户菜单刷新 (updateUserMenu)
- 登录后必须立即调用 `updateUserMenu()`，否则顶栏显示的是未登录状态
- 修复位置：`login.js` 的 `submit` 中，`store.login(member)` 后立即调用
- 全局兜底：`router.js` 的 `navigate` 函数中每次导航调用 `updateUserMenu()`

## Pitfalls
1. **不要和剧本创作混淆**：本平台是"分享+投票"工具，不是"帮你写剧本"。创作方法论参考 `short-drama-scriptwriting`，但平台功能是内容管理。
2. **素材关联是关键**：剧本和图像/视频的关联关系要清晰，否则平台价值大打折扣。
3. **投票机制要简单**：不要搞复杂的评分系统，一键点赞 + 排序就够了。
4. **中文排版**：用户对标点间距、行高、段间距极其敏感，CSS 要精细调校。
5. **单文件 vs 多文件**：用户之前是单文件 SPA，但明确要求"完全重写，多文件项目结构"。
6. **DB 迁移陷阱**：升级 IndexedDB 版本时，如果用户浏览器还存着老版本，`onupgradeneeded` 中需要注意 `e.oldVersion` 判断，避免对已有 store 重复 `createObjectStore`。
7. **提示词复制降级**：`navigator.clipboard.writeText` 在 HTTPS 下可靠，HTTP 可能失败，需准备 `document.execCommand('copy')` 降级方案。
8. **event.stopPropagation()**：在卡片内嵌按钮（如删除）必须阻止冒泡，否则触发卡片跳转。
9. **worker.js vs worker_deploy.js**：`worker.js` 是旧版源模板（可能过时），`worker_deploy.js` 是 `deploy_cf.py` 自动构建的**实际部署文件**。修改源代码后 `deploy_cf.py` 重新生成 `worker_deploy.js`，所以直接改 `worker_deploy.js` 会被下次构建覆盖。正确的修改流程：改源代码 → 运行构建 → 验证 → 部署。
10. **Cache-Control 缺失**：Worker 响应默认不带 `Cache-Control` 头，浏览器可能缓存旧版本 JS 导致功能不更新。必须在 `worker_deploy.js` 的 Response 中显式设置 `"cache-control": "no-cache, no-store, must-revalidate"`。
11. **updateUserMenu 不自动调用**：`updateUserMenu()` 不会在登录/登出/导航时自动触发，必须手动调用。修复方案：(a) `login.js` 的 `submit` 中 `store.login(member)` 后立即调用，(b) `router.js` 的 `navigate()` 函数中每次导航调用 `if (typeof updateUserMenu === 'function') updateUserMenu()`。
