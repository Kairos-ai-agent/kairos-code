---
name: "api-design"
description: "API设计全流程。涵盖RESTful API设计、GraphQL Schema设计、gRPC服务定义、OpenAPI/Swagger规范、认证授权方案、错误处理规范、版本策略。触发器：设计API、写OpenAPI规范、定接口、评审API方案。"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/software-development/api-design/SKILL.md"
---
# API设计 · API Design

## 角色定位
本技能将 Agent 转化为API设计师，专注于设计清晰、一致、可维护的API。

## RESTful API设计

### URL结构规范
```
# 资源命名（复数名词）
GET    /api/v1/users           # 列表
POST   /api/v1/users           # 创建
GET    /api/v1/users/:id       # 详情
PUT    /api/v1/users/:id       # 全量更新
PATCH  /api/v1/users/:id       # 部分更新
DELETE /api/v1/users/:id       # 删除

# 子资源
GET    /api/v1/users/:id/orders        # 用户的订单列表
GET    /api/v1/orders/:id/items        # 订单的商品列表

# 操作（非CRUD用动词）
POST   /api/v1/orders/:id/cancel       # 取消订单
POST   /api/v1/users/:id/activate      # 激活用户

# 查询参数
GET    /api/v1/users?page=1&limit=20&sort=-created_at&status=active
       # page: 页码, limit: 每页条数, sort: 排序(-降序), 其他: 过滤
```

### 请求体规范
```json
// 创建 - POST
POST /api/v1/users
{
  "name": "张三",
  "email": "zhangsan@example.com",
  "role": "admin"
}

// 更新 - PATCH（只传要改的字段）
PATCH /api/v1/users/123
{
  "name": "李四"
}
```

### 响应体规范
```json
// 成功
HTTP 200
{
  "data": { ... },
  "meta": {
    "page": 1,
    "limit": 20,
    "total": 100
  }
}

// 列表
HTTP 200
{
  "data": [ ... ],
  "meta": {
    "page": 1,
    "limit": 20,
    "total": 100
  }
}

// 创建成功
HTTP 201
{
  "data": { "id": "new-uuid", ... }
}

// 无内容
HTTP 204
// (无body)

// 错误
HTTP 4xx
{
  "error": {
    "code": "INVALID_INPUT",
    "message": "邮箱格式不正确",
    "details": [
      { "field": "email", "message": "不是有效的邮箱地址" }
    ]
  }
}

// 服务端错误
HTTP 500
{
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "服务器内部错误",
    "requestId": "req-uuid"
  }
}
```

### 错误码规范
| HTTP状态码 | 错误码 | 含义 | 处理方式 |
|-----------|--------|------|---------|
| 400 | VALIDATION_ERROR | 参数校验失败 | 前端修复请求 |
| 400 | INVALID_INPUT | 输入格式错误 | 前端修复请求 |
| 401 | UNAUTHORIZED | 未认证 | 跳转登录 |
| 403 | FORBIDDEN | 无权限 | 提示无权限 |
| 404 | NOT_FOUND | 资源不存在 | 提示未找到 |
| 409 | CONFLICT | 资源冲突（重复创建） | 提示冲突 |
| 429 | RATE_LIMITED | 请求频率超限 | 等待后重试 |
| 500 | INTERNAL_ERROR | 服务器内部错误 | 联系管理员 |
| 503 | SERVICE_UNAVAILABLE | 服务暂不可用 | 稍后重试 |

### OpenAPI 3.0规范片断
```yaml
openapi: 3.0.0
info:
  title: 用户服务API
  version: 1.0.0
paths:
  /api/v1/users:
    get:
      summary: 获取用户列表
      parameters:
        - name: page
          in: query
          schema: { type: integer, default: 1 }
        - name: limit
          in: query
          schema: { type: integer, default: 20 }
      responses:
        '200':
          description: 用户列表
          content:
            application/json:
              schema:
                type: object
                properties:
                  data:
                    type: array
                    items:
                      $ref: '#/components/schemas/User'
                  meta:
                    $ref: '#/components/schemas/PaginationMeta'
```

## GraphQL设计

### Schema设计原则
```graphql
# 类型设计
type User {
  id: ID!
  name: String!
  email: String!   # 敏感字段加权限控制
  posts: [Post!]!  # 关联查询
  createdAt: DateTime!
}

type Post {
  id: ID!
  title: String!
  content: String!
  author: User!
  comments: [Comment!]!
}

# 查询入口
type Query {
  user(id: ID!): User
  users(page: Int, limit: Int): UserConnection!
  postsByAuthor(authorId: ID!): [Post!]!
}

# 变更入口
type Mutation {
  createUser(input: CreateUserInput!): User!
  updateUser(id: ID!, input: UpdateUserInput!): User!
  deleteUser(id: ID!): Boolean!
}

# 分页
type UserConnection {
  edges: [UserEdge!]!
  pageInfo: PageInfo!
}
type UserEdge {
  node: User!
  cursor: String!
}
```

### N+1问题预防
```graphql
# 使用DataLoader批量加载
type Query {
  users: [User!]!    # 如果查询每个user.posts，用Dataloader合并
}
```

## gRPC/Protobuf设计

```protobuf
syntax = "proto3";

service UserService {
  rpc GetUser(GetUserRequest) returns (User);
  rpc ListUsers(ListUsersRequest) returns (ListUsersResponse);
  rpc CreateUser(CreateUserRequest) returns (User);
  rpc UpdateUser(UpdateUserRequest) returns (User);
  rpc DeleteUser(DeleteUserRequest) returns (Empty);
}

message User {
  string id = 1;
  string name = 2;
  string email = 3;
  string role = 4;
  Timestamp created_at = 5;
}

message GetUserRequest {
  string id = 1;
}

message ListUsersRequest {
  int32 page = 1;
  int32 limit = 2;
  string status = 3;
}
```

## API安全设计

### 认证方案选型
| 方案 | 适用场景 | 注意事项 |
|------|---------|---------|
| JWT Token | 无状态API、分布式 | token过期、刷新策略 |
| Session+Cookie | 传统Web应用 | CSRF防护、服务端存储 |
| OAuth2 | 第三方登录、开放API | 授权码流程、scope设计 |
| API Key | 内部服务间调用、开放平台 | key轮换、权限隔离 |

### 安全必做清单
- [ ] HTTPS强制（HSTS头）
- [ ] Rate Limiting（每用户/每IP）
- [ ] 输入校验（防止注入）
- [ ] CORS配置精确（不直接用*）
- [ ] 敏感字段脱敏（密码、手机号）
- [ ] 审计日志（记录谁做了什么）
- [ ] 请求大小限制
- [ ] 超时设置

## API版本策略

### 策略对比
| 策略 | 实现方式 | 优点 | 缺点 |
|------|---------|------|------|
| URL路径 | /api/v1/users | 最直观 | URL膨胀 |
| Header | Accept: vnd.myapp.v1+json | URL干净 | 调试不便 |
| 参数 | /api/users?version=1 | 简单 | 参数污染 |
| 无版本 | 向后兼容 | 最干净 | 难度高 |

### 兼容性原则
```
新增字段：安全（客户端忽略未知字段）
修改字段：不兼容（需新版本）
删除字段：不兼容（需新版本）
修改行为：不兼容（需新版本）
新增接口：安全
弃用接口：标注deprecated + 给出替代方案 + 保留至少一个major版本周期
```

## API设计检查清单
- [ ] URL使用复数名词、不使用动词（除操作外）
- [ ] HTTP方法语义正确（GET查/POST建/PUT全改/PATCH部分改/DELETE删）
- [ ] 响应格式统一（成功/错误结构一致）
- [ ] 状态码使用正确（不滥用200）
- [ ] 分页、排序、过滤支持
- [ ] 错误信息可读且包含code + message + details
- [ ] 敏感字段不返回
- [ ] 有Rate Limiting
- [ ] 有请求ID便于追踪
- [ ] 有版本策略
- [ ] 有OpenAPI/文档
