---
name: "software-architecture-design"
description: "软件架构设计全流程。涵盖系统设计、架构模式选型、C4模型、架构决策记录(ADR)、微服务设计、模块拆分、接口契约。触发器：设计系统架构、做技术选型、写ADR、画架构图、评审架构方案。"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\software-development\\software-architecture-design\\SKILL.md"
---
# 软件架构设计 · Software Architecture Design

## 角色定位
本技能将 Agent 转化为软件架构师，负责从需求到架构方案的全流程设计。

## 核心流程

```
需求分析 → 约束识别 → 架构模式选型 → 模块划分 → 接口设计 → ADR记录 → 架构图
```

## 第一步：需求分析与约束识别

### 需要澄清的问题
| 维度 | 关键问题 |
|------|---------|
| 功能需求 | 核心功能是什么？用户是谁？使用场景？ |
| 非功能需求 | QPS、延迟要求、可用性SLA、数据一致性要求 |
| 技术约束 | 语言/框架限制、部署环境、团队技术栈 |
| 业务约束 | 预算、时间线、合规要求、遗留系统集成 |
| 规模预期 | 初期规模vs未来3年预期增长 |

### 输出：需求摘要
```
## 需求摘要
- 项目：[名称]
- 核心功能：[一句话]
- 目标QPS：[数值]
- 可用性SLA：[99.x%]
- 技术栈：[语言/框架]
- 关键约束：[数据库、部署、合规等]
```

## 第二步：架构模式选型

### 常见架构模式速查

| 模式 | 适用场景 | 不适用场景 |
|------|---------|-----------|
| **单体架构** | 小团队、早期产品、简单CRUD | 大规模团队、高并发、独立部署需求 |
| **分层架构** | 标准企业应用、MVC模式 | 复杂领域逻辑（会退化为"大泥球"） |
| **微服务** | 大团队、独立部署、多技术栈 | 小团队、简单应用、强一致性要求高 |
| **事件驱动** | 异步处理、解耦、可扩展 | 实时请求-响应、简单CRUD |
| **CQRS** | 读写负载差异大、复杂查询 | 简单CRUD、单一数据源 |
| **六边形架构** | 需要高度可测试性、适配多输出 | 简单应用、过度工程 |
| **分层+DDD** | 复杂业务逻辑、领域专家协作 | 简单CRUD应用 |

### 选型决策树
```
应用规模？
├─ 小（<3人，简单CRUD）
│  └─ 单体 + 分层架构
├─ 中（3-10人，业务较复杂）
│  ├─ 标准Web → 分层 + DDD
│  ├─ 高并发 → 事件驱动 + CQRS
│  └─ API为主 → 六边形架构
└─ 大（>10人，多团队）
   ├─ 独立部署需求 → 微服务
   ├─ 复杂领域 → DDD + 事件驱动
   └─ 数据密集 → CQRS + 事件溯源
```

## 第三步：模块划分

### 划分原则
```
高内聚、低耦合
单一职责
接口隔离
依赖反转（DIP）
```

### 模块划分产出格式
```
## 模块清单

### Module A: [名称]
- 职责：[一句话描述]
- 依赖：[依赖的模块]
- 对外接口：[提供的API/事件]
- 技术选型：[使用的框架/库]
```

### C4模型

#### Context（系统上下文图）
```
[用户] → [系统] → [外部系统]
```
关注：系统的范围、用户、外部依赖

#### Container（容器图）
```
[Web App] → [API Server] → [Database]
                           → [Cache]
                           → [Queue]
```
关注：技术选型、进程间通信

#### Component（组件图）
```
[Controller] → [Service] → [Repository]
                       → [External Client]
```
关注：模块划分、依赖关系

#### Code（代码图，仅关键部分）
```
选关键类/接口展示核心设计
```

## 第四步：架构决策记录（ADR）

### ADR模板
```
# ADR [编号]: [标题]

## 状态
[提议/接受/废弃/替代]

## 上下文
[决策背景、约束条件、业务需求]

## 选项
- 方案A：[描述]
  - 优点：[...]
  - 缺点：[...]
- 方案B：[描述]
  - 优点：[...]
  - 缺点：[...]

## 决策
选择方案A，因为[理由]

## 后果
[接受的正负面后果]

## 合规性
[如何确保决策被执行]
```

### 常用ADR主题
| 编号 | 主题 | 示例 |
|------|------|------|
| ADR-001 | 技术栈选型 | Python vs Go, React vs Vue |
| ADR-002 | 数据库选型 | PostgreSQL vs MySQL vs MongoDB |
| ADR-003 | 消息队列 | Kafka vs RabbitMQ vs Redis Streams |
| ADR-004 | API风格 | REST vs GraphQL vs gRPC |
| ADR-005 | 部署方式 | K8s vs Serverless vs VM |
| ADR-006 | 数据一致性 | 强一致 vs 最终一致 |
| ADR-007 | 缓存策略 | Redis vs CDN vs 本地缓存 |
| ADR-008 | 认证方案 | JWT vs Session vs OAuth2 |

## 第五步：接口契约设计

### API契约格式
```
## API: [端点]

### 请求
- Method: GET/POST/PUT/DELETE
- Path: /api/v1/{resource}
- Headers: Content-Type, Authorization
- Body:
  ```json
  {
    "field": "type | description"
  }
  ```

### 响应
- Status: 200/201/400/404/500
- Body:
  ```json
  {
    "data": {},
    "error": null
  }
  ```

### 错误码
| HTTP Status | Code | 含义 |
|-------------|------|------|
| 400 | INVALID_INPUT | 参数校验失败 |
| 404 | NOT_FOUND | 资源不存在 |
| 409 | CONFLICT | 资源冲突 |
| 500 | INTERNAL_ERROR | 服务器内部错误 |
```

### 事件契约格式
```
## Event: [事件名]

### 生产者
[哪个模块/服务发出]

### 消费者
[哪个模块/服务消费]

### Schema
```json
{
  "eventId": "uuid",
  "type": "事件类型",
  "timestamp": "ISO8601",
  "payload": {}
}
```

### 保证级别
- 投递: [at-least-once / exactly-once]
- 顺序: [保证/不保证]
- 持久化: [是/否]
```

## 第六步：技术选型速查

| 维度 | 选项 | 选型要点 |
|------|------|---------|
| 语言 | Go/Python/Java/TS/Rust | 团队经验 > 性能 > 生态 |
| 数据库 | PG/MySQL/MongoDB/Redis | 数据模型 > 一致性 > 规模 |
| 消息队列 | Kafka/RabbitMQ/Redis | 吞吐量 > 延迟 > 可靠性 |
| API | REST/GraphQL/gRPC | 客户端多样性 > 性能 > 工具链 |
| 部署 | K8s/Serverless/VM | 团队运维能力 > 规模 > 成本 |
| 监控 | Prometheus/Datadog/Sentry | 基础设施 > 预算 > 告警需求 |
| 缓存 | Redis/CDN/Memcached | 数据量 > 访问模式 > 一致性 |

## 参考
- `architecture-diagram` skill → 生成架构图SVG
- `excalidraw` skill → 手绘风格架构图
- `writing-plans` skill → 实现计划
- `spike` skill → 技术可行性验证
