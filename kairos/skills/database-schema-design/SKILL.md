---
name: "database-schema-design"
description: "数据库设计全流程。涵盖数据建模、ER图、SQL schema设计、范式化、索引策略、迁移方案。适用于关系型和非关系型数据库。触发器：设计数据库表结构、建表、优化查询、做数据迁移。"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\software-development\\database-schema-design\\SKILL.md"
---
# 数据库设计 · Database Schema Design

## 角色定位
本技能将 Agent 转化为数据库设计师，负责从数据模型到建表方案的全流程。

## 核心流程

```
业务分析 → 概念模型(ER) → 逻辑模型(表结构) → 物理模型(索引/分区) → Migration脚本
```

## 第一步：数据建模

### 实体发现
从需求中提取实体及其关系：
```
实体：用户(User)、订单(Order)、商品(Product)、分类(Category)
关系：
  User 1:N Order（一个用户有多个订单）
  Order N:M Product（一个订单有多个商品）
  Product N:1 Category（多个商品属于一个分类）
```

### ER图标记
```
┌──────────┐     1:N     ┌──────────┐     N:M     ┌──────────┐
│  User    │────────────→│  Order   │────────────→│ Product  │
│──────────│             │──────────│             │──────────│
│ id (PK)  │             │ id (PK)  │             │ id (PK)  │
│ name     │             │ user_id  │             │ name     │
│ email    │             │ total    │             │ price    │
└──────────┘             │ status   │             │ category_id(FK)→Category
                         │ created  │             └──────────┘
                         └──────────┘
```

## 第二步：表结构设计

### 范式化原则

| 范式 | 规则 | 违反示例 | 修复 |
|------|------|---------|------|
| 1NF | 每列原子性 | `tags: "tag1,tag2"` | 拆分为关联表 |
| 2NF | 非主键列完全依赖于主键 | 订单表存用户姓名 | 只存user_id |
| 3NF | 非主键列不传递依赖 | 订单表存用户等级 | 用户信息归用户表 |
| BCNF | 每个决定因素都是候选键 | 老师-课程-教材 | 拆分表 |

### 反范式化（何时打破范式）
```
需要反范式化的场景：
- 高频读取 + 低频更新的冗余字段（如订单冗余用户昵称）
- 报表/统计需求（预计算字段）
- 避免大表JOIN（冗余热门关联数据）
```

### 字段类型选择
```
整数类型：
  INT       → 常规ID、计数（4字节）
  BIGINT    → 大规模ID、时间戳（8字节）
  SMALLINT  → 状态码、枚举（2字节）
  TINYINT   → 布尔值（1字节）

字符串类型：
  VARCHAR(N) → 变长字符串（限制长度，N不要过大）
  TEXT       → 长文本（文章内容、JSON）
  CHAR(N)    → 定长字符串（固定编码如MD5）

时间类型：
  TIMESTAMP → 带时区的时间（4字节，2038年问题）
  DATETIME  → 不带时区的时间（5-8字节）
  DATE      → 日期

JSON类型：
  JSONB(PG) / JSON(MySQL) → 灵活结构、非核心数据
```

### 建表模板（PostgreSQL）
```sql
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100) NOT NULL,
    email       VARCHAR(255) NOT NULL UNIQUE,
    role        VARCHAR(20) NOT NULL DEFAULT 'user'
                    CHECK (role IN ('admin', 'user', 'viewer')),
    status      VARCHAR(20) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'inactive', 'banned')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_status_created ON users(status, created_at DESC);
```

## 第三步：索引策略

### 索引类型

| 索引类型 | 适用场景 | 说明 |
|---------|---------|------|
| B-Tree | 大多数场景 | 支持=、>、<、BETWEEN、LIKE 'prefix%' |
| Hash | 等值查询 | 不支持范围查询 |
| GiST | 全文搜索、地理空间 | PostgreSQL特有 |
| GIN | 数组、JSON查询 | 全文索引 |
| BRIN | 大表、有序数据 | 按物理块索引，省空间 |
| 复合索引 | 多条件查询 | 字段顺序=选择性从高到低 |

### 索引设计原则
```
1. 为WHERE条件建立索引
2. 为ORDER BY字段建立索引（避免filesort）
3. 为JOIN的ON字段建立索引
4. 复合索引：选择性高的字段放前面
5. 索引不是越多越好（影响写入性能）
6. 覆盖索引：包含所有查询字段（避免回表）
7. 小表不需要索引（全表扫描更快的阈值）
```

### 慢查询诊断
```sql
-- 打开慢查询日志
SET slow_query_log = ON;
SET long_query_time = 0.1;  -- 100ms

-- 检查查询计划
EXPLAIN ANALYZE
SELECT * FROM orders
WHERE user_id = 'xxx'
  AND status = 'active'
ORDER BY created_at DESC;
```

## 第四步：迁移策略

### Migration文件模板
```sql
-- V001__create_users.sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- V002__add_role_to_users.sql
ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user';

-- V003__create_orders.sql
CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    total DECIMAL(10,2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_orders_user_id ON orders(user_id);
```

### Migration原则
```
- 所有变更通过migration，不手动改库
- 每个migration可回滚（有up就有down）
- 生产环境只追加，不修改已发布的migration
- 大表变更用pt-online-schema-change（避免锁表）
- 新字段允许NULL或有默认值（避免锁表）
```

## 常用数据库选型

| 数据库 | 适用场景 | 不适用 |
|--------|---------|--------|
| PostgreSQL | 关系型数据、复杂查询、JSON、地理空间 | 极高吞吐KV |
| MySQL | Web应用、成熟生态、读写分离 | 复杂分析查询 |
| MongoDB | 灵活Schema、文档模型、快速迭代 | 强事务、复杂关联 |
| Redis | 缓存、Session、实时排行榜 | 持久化存储 |
| ElasticSearch | 全文搜索、日志分析 | 事务性数据 |
| ClickHouse | 实时分析、OLAP | 单行频繁更新 |
| SQLite | 嵌入式、移动端、小项目 | 高并发写入 |

## 数据库设计检查清单
- [ ] 每张表都有主键（UUID或自增）
- [ ] 外键约束完整（或至少有逻辑外键）
- [ ] 字段类型选择合理（不全部VARCHAR）
- [ ] 索引覆盖了所有查询场景
- [ ] 索引不超过必要数量
- [ ] 大字段（TEXT/JSON）不参与频繁查询
- [ ] 有created_at/updated_at
- [ ] 软删除用deleted_at IS NULL（而不是DELETE）
- [ ] 唯一约束使用UNIQUE索引
- [ ] Migration可回滚
- [ ] 敏感字段加密存储
