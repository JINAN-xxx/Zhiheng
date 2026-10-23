# 原平台 AI 功能对齐实现计划

> **面向 AI 代理的工作者：** 使用 `subagent-driven-development` 执行，各域先规格审查、再独立质量审查；用实际接口、数据库和浏览器验证功能，保留未提交的旧工程优化。

**目标：** 在 AI 核心内置、LangChain 1.4.7、新独立数据与原前端界面的既定架构上，接通原开发接入、智能体、技能、Work 和知识库功能。

**架构：** 业务能力进入各自的内置模块，经 `public.py` 协作；跨模块事务由 `application/` 编排，模型调用统一经过授权、配额与用量账本。运行复用持久 Run、outbox、租约、取消和恢复。页面及组件沿用旧布局和样式，内部使用 TypeScript 和新接口。

**技术栈：** FastAPI、SQLAlchemy、PostgreSQL、Redis/ARQ、LangChain 1.4.7、Vue 3、TypeScript、Ant Design Vue；知识索引按既定设计接入 LightRAG 与 Milvus。

## 基线与执行规则

- 基准提交 `effdb0ef`；当前管理员密码属于私有运行配置，不进入源码或测试夹具。
- 原 `legacy/` 中 55 项修改及 2 项未跟踪源码保持原样；只从中复制、适配，不直接修改或提交它们。
- 保留 `refactor/ai-core-modularization` 分支及用户当前 VS Code 工作目录，不再切走工作区。
- 旧 `zhiheng` 实例保持停止；当前 `zhiheng-core` 数据在升级前备份，新增版本化迁移，不修改 `0001_core`。
- 每个已接通域具备 API、原页面、权限、失败处理、业务测试与真实浏览器验收，才将下表标记完成。菜单可点击不等于功能完成。
- 原工程未实现的占位行为不作为已具备能力恢复。MinerU 在旧源码中未实现，作为独立解析连接适配接入，不宣称原来已有。

## 功能清单与顺序

| 顺序 | 能力 | 迁移验收 | 状态 |
| --- | --- | --- | --- |
| 1 | 开发接入 | 应用新增、修改、启停、软删除；Key 一次回显、轮换、到期、撤销、按 Key 授权模型、限流/并发、调用记录；真实对外模型调用 | 已完成并部署 |
| 2 | 模型完整管理 | 连接/模型编辑、启停、删除引用检查；模型能力、参数和费率版本；文本、Embedding、Rerank 的真实适配及用量 | 已完成并部署 |
| 3 | 技能 | ZIP 预检草稿、确认安装、内容、启停、删除引用保护；不可变使用快照和压缩包路径/大小边界 | 已完成并部署 |
| 4 | 智能体 | 管理、版本冲突、发布快照、取消发布、预览/SSE、对外调用；引用真实模型、技能和知识库 | 未完成 |
| 5 | Work | 任务管理、成员与已发布版本、人工选成员运行、署名共享历史、取消、重复请求和重启后收敛 | 未完成 |
| 6 | 文件与知识库 | 受管文件、知识库与文档管理、解析分块、版本化索引、检索、LightRAG 图谱、任务状态和引用保护 | 未完成 |
| 7 | MinerU 与端到端交付 | 可配置解析连接、真实协议适配与错误状态；原页面全链路、迁移升级、故障与权限回归，更新当前实例 | 未完成 |

原功能审计报告保存在仓库外 `20261023-feature-parity/inventory-*.md`，用于检查旧字段和交互。本文跟踪实际交付，不把整个平台尚未实现的目标标为完成。

## 第一组任务：开发接入

### 1. 应用与访问密钥

**文件：** `backend/src/zhiheng/modules/developer/{applications.py,keys.py,contracts.py,orm.py,public.py}`、`backend/src/zhiheng/api/routes/developer.py`、`backend/migrations/versions/0002_developer.py`、`backend/tests/core/integration/test_developer.py`。

- [x] 先新增失败集成用例：普通成员不能管理应用；应用编码唯一；停用后 Key 即刻失效；软删除前须停用；创建 Key 返回一次明文，列表和数据库不保存明文；轮换和撤销后旧凭据按宽限规则失效。
- [x] 管理接口使用 `/api/developer/applications`、`/{id}`、`/{id}/enable`、`/{id}/disable`、`/{id}/keys`、Key 的 `/rotate` 和 `/revoke`；直接返回严格 DTO，不继承旧 JWT/envelope。
- [x] Key 请求包含 `name`、`model_ids`、`rpm_limit`、`tpm_limit`、`concurrency_limit`、可选 `expires_at`；响应仅创建/轮换时包含 `api_key`，平时只返回前缀和状态。
- [x] 数据库保存密钥摘要、授权及版本事实；应用、Key 与限额检查使用一致锁序；任何历史记录保留稳定引用。
- [x] 运行失败用例确认红，再实现并运行对应集成测试、Ruff、Mypy 与依赖边界。

```python
async def test_revoked_key_cannot_call_public_models(admin, public_client):
    application = await admin.post('/api/developer/applications', json={'code': 'tools', 'name': '内部工具'})
    assert application.status_code == 201
    response = await public_client.get('/v1/models', headers={'Authorization': 'Bearer invalid'})
    assert response.status_code == 401
    assert 'error' in response.json()
```

### 2. 受治理的公开模型 API

**文件：** `backend/src/zhiheng/application/public_runs.py`、`backend/src/zhiheng/api/routes/public.py`、`backend/src/zhiheng/api/public_errors.py`、`backend/src/zhiheng/modules/developer/limits.py`、`backend/tests/core/integration/test_developer.py`、`backend/tests/core/integration/test_public_protocol.py`。

- [x] `/v1/models` 仅列出 Key 授权且当前可用的模型；对外模型编码使用平台稳定 ID，与供应商的 `upstream_model` 分离。
- [x] `/v1/chat/completions` 支持普通响应和 OpenAI SSE；先验证身份、应用/Key/模型、输入及限额，再持久化调用、预留、Run 与 outbox。
- [x] 同步调用同样进入现有 worker/LangChain 执行和账本，不建立未治理的第二条 SDK 路径。应用调用记录能查到成功、失败和未知用量。
- [x] RPM、TPM 和并发按 Key 串行认领；撤销、停用与到期在新请求及流读取中生效。未知用量保持未知和预留，不计成零。
- [x] 集成验证授权隔离、无 Cookie 访问、非授权模型、过期/撤销、流式增量、停止/断开、供应商失败、并发限额和账本一致。

### 3. 原开发接入页面

**文件：** `frontend/src/modules/developer/{api/index.ts,routes.ts,public.ts,views/ApplicationsView.vue,components/*.vue}`；必要公共展示组件进入 `frontend/src/shared/components/`；修改 `frontend/src/app/{router.ts,components/PlatformSidebar.vue}`。

- [x] 复制并适配 `legacy/frontend/plugins/ai/views/application/` 的列表、概览、访问密钥、模型授权、调用记录与 API 说明，保留原 template 布局和 SCSS。
- [x] 模块 API 映射新 DTO；组件用显式 props/events，HTTP 只从模块 API 调用，表单及请求在关闭/换应用/注销时正确失效。
- [x] Key 明文只在创建/轮换弹窗短暂保存，复制真实值；关闭和卸载清空，禁止写入本地存储。
- [x] 在浏览器完成新建应用→发 Key→复制→真实 HTTP 调用→查看记录→轮换→撤销→停用/启用的完整链路；再启用菜单入口。

## 后续各域的执行与完成检查

技能、智能体、Work、知识库按上表依赖顺序逐域实现，具体契约以已批准架构和旧源码审计为基准；每域实施前在本文追加实际文件与失败测试，不为未编写的代码预建空模块。

- [ ] 每域的测试先复现缺口，记录 red→green；审查真实失败行为、归属校验、引用保护、重复提交、取消与晚到响应。
- [ ] 每域先独立规格审查、修复后复核，再独立质量审查；检查点采用可审查提交。
- [ ] 全量后端测试、Ruff/format、Mypy、AST 边界、wheel 检查；前端安装、核心与页面行为、lint、format、types、生产构建通过。
- [ ] 独立 Compose 空库与已有版本升级均验证，真实浏览器经过 Nginx/API/worker/LangChain；旧数据与 55 项优化再次核对。
- [ ] 备份正常实例后升级并验证原页面与真实接口，清理独立测试环境；README、结构说明和验收记录反映实际功能。

## 第一组实际交付记录

开发接入与模型管理已通过独立规格、质量审查、真实容器调用和 0001→0002 正常实例升级；详见[验收记录](../verification/2026-10-23-developer-models.md)。下一域按[技能模块计划](2026-10-23-skills.md)执行，同时恢复聊天中的原技能选择。其他域尚未标记完成。

## 技能实际交付记录

技能管理和普通聊天选择已按[技能计划](2026-10-23-skills.md)接通并完成真实容器验收，详情见[验收记录](../verification/2026-10-23-skills.md)。下一域按[智能体计划](2026-10-23-agents.md)实施；Work、知识库与MinerU仍待完成。
