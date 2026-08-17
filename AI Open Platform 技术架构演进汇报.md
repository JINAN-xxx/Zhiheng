# AI Open Platform 技术架构演进汇报

## 一、背景

前期我们基于 RuoYi-Vue3-FastAPI 进行了 AI 能力魔改，已实现：

- 模型管理
- AI Chat
- RAG
- Milvus / Elasticsearch 检索
- Rerank / Citation
- MCP
- Agent Runtime
- Agno 集成

原有调用链主要为：

```text
RuoYi Platform
      │
      ▼
   AI Chat
      │
      ▼
    Agno
      │
 ┌────┼────┐
 │    │    │
Model RAG  Tool/MCP
 │
 ▼
Provider
```

原平台已经完成了 AI 技术路线验证，但整体仍以后台管理系统为核心。

---

## 二、原魔改平台存在的问题

随着目标从“后台增加 AI 能力”升级为“AI 开放平台”，原架构逐渐无法满足需求。

当前缺失的主要平台能力包括：

```text
Tenant
Project
Application
Developer API Key
Scope
Model Entitlement
Quota
RPM / TPM
Usage Ledger
Billing
OpenAI Compatible API
```

源码评审确认，现有系统的 Tenant、Project、Application、开发者 API Key、Quota、Usage、Billing 等 AI PaaS 核心能力基本需要重新建设。

如果继续在原 RuoYi 项目上开发，将同时改造：

- 认证体系
- 权限体系
- 数据模型
- 模型调用
- RAG
- Agent
- 限流
- Usage
- OpenAPI
- 部署架构

本质上等同于：

> **在旧项目中重新建设一个新的 AI Platform。**

此前评审也明确指出，继续在当前 RuoYi 中实施新平台能力，实际接近在旧目录中重写一个平台。

---

## 三、当前技术决策

因此调整为：

> **新建独立 AI Platform。**

旧 RuoYi 不再作为 AI Platform 核心，仅作为：

- 代码资产参考
- 运营后台参考
- AI 技术验证原型
- 历史数据迁移来源

评审估算：

```text
整体可复用        约 20%~30%
运营后台可复用    约 50%
AI PaaS核心       低于 20%
```



---

# 四、新 Platform 定位

新的 Platform 重点建设平台控制能力，而不是重新开发所有 AI Runtime。

Platform 负责：

```text
Tenant

Project

Application

API Key

Scope

Model Catalog

Resource Permission

Quota

RPM / TPM

Usage

Audit

Billing

Public OpenAPI
```

核心原则：

> **Platform 是业务主数据和商业控制面。**

---

# 五、为什么当前使用 LiteLLM

原平台主要使用 Agno。

Agno 更偏向：

```text
Agent
Memory
Tool
Knowledge
Agent Orchestration
```

适合解决：

> AI Agent / AI Assistant 的业务编排问题。

而新的 AI Open Platform 第一阶段需要解决的是：

```text
统一模型接入

OpenAI Compatible API

Model Deployment

Provider Routing

Load Balance

Retry

Fallback

Streaming

模型健康检查

Token / Cost
```

因此当前模型层调整为：

```text
Platform
   │
   ▼
Model Service
   │
   ▼
LiteLLM
   │
   ▼
Model Provider
```

---

## 六、LiteLLM 与 Agno 的定位区别

| 技术 | 当前定位 |
|---|---|
| Agno | Agent Framework |
| LiteLLM | Model Gateway / Model Runtime |
| BISHENG | Knowledge / Agent Runtime |
| Platform | AI开放平台 |

因此本次调整不是：

```text
Agno → LiteLLM
```

而是：

```text
原来：

Agno
├── Model
├── Agent
├── Knowledge
└── Tool


现在：

Model      → LiteLLM
Knowledge  → BISHENG
Agent      → BISHENG
Platform   → 自研
```

即：

> **从一个综合 AI Runtime，调整为职责拆分。**

---

# 七、LiteLLM 当前职责

LiteLLM 作为独立 Model Gateway 部署：

```text
                 LiteLLM
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
    DeepSeek       Qwen       OpenAI
                              Claude
                              vLLM
```

负责：

- Provider Adapter
- Model Deployment
- Routing
- Retry
- Fallback
- Load Balance
- Streaming
- Provider Credential
- Raw Token / Cost

不负责：

- Tenant
- Customer API Key
- Project
- Application
- 最终 Quota
- 最终 Usage
- Billing

这些仍由 Platform 管理。

---

# 八、为什么考虑 BISHENG

LiteLLM 只能解决模型调用问题。

平台后续还需要：

```text
Document Parsing

Knowledge Base

Chunk

Embedding

Vector Index

Hybrid Retrieval

Rerank

RAG

Citation

Agent

Workflow

MCP
```

如果继续全部自研，会重新回到旧 RuoYi 的路线。

因此当前考虑使用 BISHENG 承担 AI Engine 能力。

BISHENG 当前主要用于：

```text
Knowledge Engine

RAG Engine

Agent Engine

Workflow Engine

MCP Runtime
```

而不是作为我们的 Platform。

---

# 九、BISHENG 在新架构中的位置

不直接：

```text
Platform
   ↓
BISHENG
```

而是：

```text
Platform
   │
   ▼
Knowledge Service
   │
   ▼
KnowledgeEngine
   │
   ▼
Bisheng Adapter
   │
   ▼
BISHENG
```

Agent 同样：

```text
Platform
   │
   ▼
Agent Service
   │
   ▼
AgentEngine
   │
   ▼
Bisheng Adapter
   │
   ▼
BISHENG
```

这样 BISHENG 只是底层 Engine。

---

# 十、为什么当前优先 BISHENG

当前主要考虑以下几个因素：

### 1. Knowledge 能力匹配

当前开发顺序为：

```text
Model
  ↓
Knowledge
  ↓
Agent
```

BISHENG 已覆盖：

- 文档解析
- Knowledge
- RAG
- Retrieval
- Rerank
- Citation
- Agent
- Workflow
- MCP

与第二阶段 Knowledge Engine 的需求较匹配。

### 2. 避免重复开发

可以减少自研：

```text
Parser
Chunk
RAG
Retrieval
Agent Runtime
Workflow Runtime
MCP Runtime
```

### 3. 作为 Engine 使用

我们不依赖 BISHENG 的产品体系，只通过 Adapter 调用底层能力。

### 4. License 边界更适合当前路线

BISHENG 主仓库当前为 Apache-2.0。

相比之下，Dify 当前 License 对源码运行多租户环境存在额外商业授权条件。

因此当前：

> **自建多租户 Platform + 私有 AI Engine**

模式下，BISHENG 更符合当前技术和授权边界。

---

# 十一、为什么当前没有优先使用 Dify

Dify 本身是优秀的 AI Application Platform，适合：

```text
Chatbot
Workflow
Agent
AI App
低代码应用开发
```

但我们的目标是：

```text
AI Open Platform

Tenant
Project
Application
API Key
Quota
Usage
Billing
Model API
Knowledge API
Agent API
```

如果直接使用 Dify 作为核心，会形成：

```text
我们的 Platform
       ↓
Dify Platform
```

存在较强的平台能力重叠。

因此当前优先选择：

> **将 BISHENG 作为 Engine，而不是再引入一套完整 AI Application Platform。**

---

# 十二、最新整体架构

```text
                第三方 / 内部业务系统
                         │
                         ▼
               ┌───────────────────┐
               │    AI Platform    │
               │                   │
               │ Tenant            │
               │ Project           │
               │ Application       │
               │ API Key           │
               │ Scope             │
               │ Quota             │
               │ Usage             │
               │ Audit             │
               │ Billing           │
               └─────────┬─────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
 Model Service    Knowledge Service    Agent Service
        │                │                │
        ▼                ▼                ▼
  ModelEngine     KnowledgeEngine      AgentEngine
        │                │                │
        ▼                ▼                ▼
     LiteLLM          BISHENG          BISHENG
        │                │                │
        └────────────────┼────────────────┘
                         ▼
                 Model Provider
```

---

# 十三、三个核心 Capability

未来真正长期维护的是：

```text
Model Service

Knowledge Service

Agent Service
```

而不是直接绑定：

```text
LiteLLM

BISHENG

Agno
```

---

## Model Service

外部使用：

```text
/v1/models

/v1/chat/completions

/v1/responses

/v1/embeddings
```

内部当前：

```text
Model Service
      ↓
LiteLLM
```

未来：

```text
Model Service
      ↓
其它 Model Gateway
```

用户无感。

---

## Knowledge Service

外部使用：

```text
/v1/knowledge-bases

/v1/knowledge-bases/{id}/documents

/v1/knowledge-bases/{id}/retrieve

/v1/knowledge-bases/{id}/query
```

内部当前：

```text
Knowledge Service
      ↓
BISHENG
```

未来可以：

```text
Knowledge Service
      ↓
RAGFlow / 自研 Engine
```

用户接口保持稳定。

---

## Agent Service

外部使用：

```text
/v1/agents

/v1/agents/{id}/run

/v1/agents/{id}/stream
```

内部当前：

```text
Agent Service
      ↓
BISHENG
```

未来可以：

```text
Agent Service
      ↓
Agno / LangGraph / 其它 Agent Runtime
```

用户无需修改调用方式。

---

# 十四、资源 ID 解耦

外部用户只使用 Platform Resource ID。

例如：

```text
kb_001
agent_001
model_company_reasoning
```

内部通过：

```text
resource_binding
```

映射底层 Engine。

例如：

```text
kb_001
  ↓
BISHENG knowledge_id = 18293
```

未来：

```text
kb_001
  ↓
RAGFlow dataset_id = xxxx
```

用户继续使用：

```text
kb_001
```

不发生变化。

---

# 十五、用户无感的含义

底层技术可以变化：

```text
LiteLLM
   ↓
Other Gateway

BISHENG
   ↓
RAGFlow

BISHENG Agent
   ↓
Agno
```

用户始终看到：

```text
我们的 API

我们的 API Key

我们的 Model ID

我们的 Knowledge ID

我们的 Agent ID

我们的错误协议

我们的 Usage
```

因此需要保证：

> **技术实现可替换，产品契约保持稳定。**

---

# 十六、后续规划

## V0.1 Model Platform

```text
Platform
+
Model Service
+
LiteLLM
```

建设：

- Tenant
- Project
- Application
- API Key
- Scope
- Model Catalog
- Model Entitlement
- Quota
- RPM / TPM
- Usage
- Audit

核心闭环：

```text
第三方
  ↓
Platform
  ↓
Model Service
  ↓
LiteLLM
  ↓
Provider
```

---

## V0.2 Knowledge Platform

新增：

```text
Knowledge Service
+
BISHENG
```

建设：

- Knowledge Base
- Document
- Upload
- Retrieve
- RAG Query
- Citation
- Resource Binding

---

## V0.3 Agent Platform

新增：

```text
Agent Service
+
Agent Engine
```

建设：

- Agent
- Workflow
- MCP
- Tool
- Trace
- Agent Usage

---

## V1.0 商业化

增加：

- Product
- Plan
- Pricing
- Billing
- Cost
- Balance
- Bill
- Invoice

---

# 十七、当前技术路线总结

原平台解决的问题是：

> **给企业后台增加 AI 能力。**

新平台解决的问题是：

> **建设统一、开放、可计量、可扩展、可替换底层技术路线的 AI 能力平台。**

技术路线由：

```text
RuoYi
  ↓
Agno
  ↓
Model / RAG / Agent
```

演进为：

```text
                       AI Platform
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
    Model Service    Knowledge Service    Agent Service
          │                 │                 │
       LiteLLM           BISHENG           BISHENG
```

最终核心原则：

> **Platform 是我们的产品。**

> **Model / Knowledge / Agent Service 是我们的能力接口。**

> **LiteLLM、BISHENG、Agno 等只是底层技术实现。**

> **底层技术路线可以调整，但对于用户来说 API、资源 ID 和使用方式保持稳定。**