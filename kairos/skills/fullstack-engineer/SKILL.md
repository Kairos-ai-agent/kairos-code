---
name: "fullstack-engineer"
description: "全栈工程师专家技能。当用户要求进行网站开发、软件开发、小程序开发，或请求创建 Web 应用、移动应用、桌面应用、小程序项目时触发。涵盖前端开发、后端开发、数据库设计、API 开发、部署运维等全栈技术栈。"
priority: 0.5
imported-from: "minimax"
source-path: "minimax/skills/fullstack-engineer/SKILL.md"
---
# 全栈工程师专家

## 核心能力

### 1. 网站开发
- **前端框架**: React, Vue.js, Angular, Next.js, Nuxt.js
- **后端框架**: Node.js (Express/Koa/NestJS), Python (Django/Flask/FastAPI), Go (Gin/Fiber)
- **全栈框架**: Next.js, Nuxt.js, Remix, Spring Boot

### 2. 软件开发
- **桌面应用**: Electron, Tauri, Qt, GTK
- **移动应用**: React Native, Flutter, uni-app
- **后端服务**: RESTful API, GraphQL, gRPC, WebSocket

### 3. 小程序开发
- **微信小程序**: 原生开发, Taro, uni-app, mpvue
- **支付宝小程序**: 原生开发, chameleon
- **抖音/头条小程序**: 原生开发, Taro
- **跨平台小程序**: uni-app, WePY, mpvue

## 开发流程

### 需求分析
1. 明确项目目标和技术需求
2. 确定技术栈选择
3. 制定开发计划和时间表

### 项目结构
```
project/
├── src/
│   ├── components/     # 组件
│   ├── pages/          # 页面
│   ├── hooks/          # 钩子函数
│   ├── utils/          # 工具函数
│   ├── services/       # API 服务
│   ├── stores/         # 状态管理
│   └── types/          # 类型定义
├── public/             # 静态资源
├── tests/              # 测试文件
└── package.json
```

### 数据库设计
- **关系型**: MySQL, PostgreSQL
- **NoSQL**: MongoDB, Redis
- **向量数据库**: Pinecone, Milvus, Qdrant

### API 开发
- RESTful API 设计规范
- GraphQL Schema 设计
- API 文档生成 (Swagger/OpenAPI)
- API 版本管理

### 部署策略
- **前端部署**: Vercel, Netlify, Cloudflare Pages
- **后端部署**: Docker, Kubernetes, Serverless
- **数据库**: 云数据库服务, 自托管

## 技术选型指南

### 网站开发
| 场景 | 推荐技术栈 |
|------|-----------|
| 简单展示站 | HTML + CSS + Vanilla JS / Next.js 静态生成 |
| 企业官网 | Next.js + Tailwind CSS + Contentful |
| 电商平台 | Next.js + Shopify / Medusa |
| SaaS 产品 | Next.js + Prisma + PostgreSQL |
| 论坛社区 | Discourse, NodeBB, 自定义开发 |

### 桌面应用
| 场景 | 推荐技术栈 |
|------|-----------|
| 跨平台工具 | Electron (Web 技术栈) / Tauri (Rust) |
| 专业软件 | Qt (C++) / WPF (.NET) |
| 轻量工具 | Tauri + React/Vue |

### 小程序
| 场景 | 推荐技术栈 |
|------|-----------|
| 微信小程序 | 原生 + Vant UI / uni-app + uView |
| 多端小程序 | uni-app (一套代码,多端运行) |
| 高性能小程序 | 原生开发 + 分包加载 |

## 质量保障

### 代码规范
- ESLint + Prettier 代码格式化
- TypeScript 类型安全
- Git 提交规范 (Conventional Commits)

### 测试策略
- 单元测试: Jest, Vitest, Pytest
- 集成测试: Testing Library, Cypress
- E2E 测试: Playwright, Cypress

### 性能优化
- 前端: 代码分割, 懒加载, 缓存策略
- 后端: 数据库索引, 缓存层, 连接池
- 监控: Sentry, Prometheus, Grafana

## 交付标准

1. **功能完整**: 所有需求功能正常实现
2. **代码质量**: 遵循最佳实践, 无明显性能问题
3. **测试覆盖**: 核心功能有测试保障
4. **文档齐全**: README + API 文档
5. **部署就绪**: 配置好部署流程或提供部署指南

## 常用命令参考

### Next.js
```bash
npx create-next-app@latest my-app
npm run dev      # 开发
npm run build    # 构建
npm run start    # 生产
```

### Electron
```bash
npm init electron-app my-app
npm run start    # 开发
npm run make     # 打包
```

### uni-app
```bash
npx degit dcloudio/uni-preset-vue#vue2 my-project
npm install
npm run dev:mp-weixin   # 微信小程序
npm run build:mp-weixin # 构建微信小程序
```

### Tauri
```bash
npm create tauri-app my-app
npm run tauri dev         # 开发
npm run tauri build       # 构建
```
