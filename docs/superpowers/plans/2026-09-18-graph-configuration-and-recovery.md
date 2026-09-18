# 知识图谱独立配置与恢复实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 处理独立的模型适配、前端和审查任务；主代理负责图谱任务及存储集成。步骤使用复选框跟踪。

**目标：** 参考 Yuxi 的图谱模块，让图谱自动适配模型、支持单切片试构建、阶段进度、缓存续建和独立版本重建。

**架构：** 保留 LightRAG 的文档入库和恢复协议、PostgreSQL 图谱/缓存及 Milvus 向量。知识库元数据保存图谱配置、构建配置快照和活动版本；每个重建版本使用独立 workspace，只有全部文档成功才发布。当前聊天及主向量索引不使用图谱配置。

**技术栈：** FastAPI、Pydantic、LightRAG、PostgreSQL、Milvus、Vue 3、Ant Design Vue、pytest、Node test、Playwright。

## 已确认的范围和边界

- 用户已批准两期顺序执行，无需再次询问实施方式。
- 现有三个文档分别使用 GLM 5.3、DeepSeek 完整构建，验证结果持久化及图谱检索。
- GLM 5.3 经现有代理使用 `extra_body.reasoning_effort=low` 已完成单次抽取验证；普通顶层参数被代理拒绝。尚未验证完整三个文档。
- 当前 LightRAG 已有 PG 缓存。继续使用有文档恢复协议的入口，不切换到缺少恢复日志的 `ainsert_custom_kg`。
- 保留完成图谱；配置改变时创建新版本构建，成功后原子切换元数据指针。失败版本可以续建。对旧版本的清理必须限定其 workspace。
- 原始文档、主向量索引、用户命名与工作区既有删除/构建产物必须保留。

## 文件及职责

- `ruoyi-fastapi-backend/plugins/ai/knowledge/graph_config.py`：严格配置、模型策略与版本指纹。
- `ruoyi-fastapi-backend/plugins/ai/knowledge/models.py`：构建和试构建请求。
- `ruoyi-fastapi-backend/plugins/ai/knowledge/graph.py`：LightRAG 适配、模型参数、真实状态及试构建。
- `ruoyi-fastapi-backend/plugins/ai/knowledge/runtime.py`：配置保存、任务调度、版本发布及恢复。
- `ruoyi-fastapi-backend/plugins/ai/controller/ai_knowledge_controller.py`：按现有权限暴露配置和试构建接口。
- `ruoyi-fastapi-backend/plugins/ai/service/model_service.py`、`infrastructure/litellm_runtime_adapter.py`：受限的低推理参数透传。
- `ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeGraphPage.vue`、`views/knowledge/index.vue`、`api/knowledge.js`、`utils/knowledge.js`：图谱配置、试构建结果、阶段进度及重建操作。
- 相应 `tests/plugins/ai/test_knowledge_*.py`、模型适配测试、前端行为测试及实际部署探针：验收证据。

## 任务 1：图谱模型参数和配置

- [ ] 添加失败测试，验证受限 `reasoning_effort='low'` 在实际 HTTP 请求体中出现，任意额外 JSON 仍被拒绝；普通聊天不主动注入。
- [ ] 扩展 ModelService 和 LiteLLM adapter，把该参数放进 `extra_body`，可与现有受限 thinking 参数合并。
- [ ] 定义图谱配置：`llm_model_id`、`reasoning_strategy=auto|default|low|disabled`、`timeout_seconds=240`（30–600）、`max_output_tokens=16384`（1024–32768）、`concurrency=2`（1–4）。
- [ ] 自动策略使用实际模型代码/绑定标识识别已验证模型族；未知模型保持默认参数，允许图谱高级选项明确覆盖。禁止平台 UUID 白名单作为新方案。
- [ ] 运行参数测试和图谱测试，并审查参数的最终传输路径。

关键回归断言：

```python
assert outgoing_body['reasoning_effort'] == 'low'
assert 'reasoning_effort' not in ordinary_chat_body
assert graph_parameters(unknown_model, auto_config) == {'max_tokens': 16384}
```

## 任务 2：配置和版本构建

- [ ] 测试 GET/PUT graph-config、首次启用、修改配置需重建、构建中拒绝修改以及权限边界。
- [ ] GET 返回配置、活动配置/版本及是否需要重建。PUT 保存已解析验证的配置；构建请求支持显式 rebuild。
- [ ] 旧知识库由现有 llm_model_id 生成兼容配置；旧活动图谱继续使用原 workspace。
- [ ] 构建任务保存配置及模型绑定快照，重建使用独立 workspace；查询读取活动版本。
- [ ] 文档全集成功后才发布；失败/中断不覆盖活动版本，继续任务复用同版本成功文档。
- [ ] 测试配置变化、绑定变化、重启恢复、重复提交、文档变动锁和活动图谱读取。

关键回归断言：

```python
assert failed_build['active_revision'] == previous_revision
assert completed_rebuild['active_revision'] != previous_revision
assert original_vector_rows == vector_rows_after_rebuild
```

## 任务 3：单切片试构建

- [ ] 测试从知识库现有文档读取单切片、配置验证、空文档和模型失败的安全错误。
- [ ] 使用独立临时 workspace 走同一抽取/入库入口，返回耗时、实体数、关系数和错误阶段；结束清理临时图谱与向量，不修改文档完成状态。
- [ ] 采用后台任务/状态轮询承载试构建，避免 nginx 请求超时；任务状态只保存安全结果，原文和凭据不进入响应或日志。
- [ ] 通过真实文档验证试构建及清理，并验证重复请求不会并发放大成本。

## 任务 4：切片进度和缓存恢复

- [ ] 检查已安装 LightRAG 的恢复、缓存及状态接口，优先复用其正式文档入库路径。
- [ ] 将实际切片数、完成抽取数和处理阶段映射为图谱任务状态；不从时间推算进度。
- [ ] 对失败/中断任务保留可恢复状态和成功抽取缓存，重试只补齐缺失结果；不可恢复状态走既有安全清理，保留模型缓存。
- [ ] 缓存按版本隔离，版本指纹覆盖文本切片配置、模型绑定、推理参数和抽取提示词版本。
- [ ] 注入模型失败、向量失败和进程中断，验证缓存命中、重复节点/关系不增加、最终状态与存储一致。

## 任务 5：图谱页面

- [ ] 增加图谱设置区：选择已发布模型，默认自动策略；高级设置含推理策略、超时、输出上限和并发。
- [ ] 接入保存、试构建、构建/续建和显式重建。试构建显示真实实体/关系数及耗时，失败显示具体安全原因。
- [ ] 展示文档和切片阶段进度；重建过程中允许查看上一活动版本。
- [ ] 保留当前桌面端 Ant Design 风格，处理切换知识库/页面后的异步过期响应和轮询取消。
- [ ] 运行 Node 行为测试、前端构建和 Playwright 桌面验证。

## 任务 6：整体验证、审查与发布

- [ ] 运行相关 Python 测试和 lint、前端测试和构建，进行规格及质量审查并修复发现。
- [ ] 在隔离 workspace 使用原三个文档分别完整构建 GLM 和 DeepSeek；记录耗时、文档终态、节点/关系及图谱查询。
- [ ] 验证 PG/Milvus 实际恢复、版本切换、临时 workspace 清理及原知识库主向量不变。
- [ ] 准备可回滚发布脚本并沿用当前 nginx/FRP 入口发布，验证健康、权限及桌面 UI。
- [ ] 精确提交本任务文件并更新运维记录，不收集工作区原有改动。

测试命令（工作目录为隔离工作区的相应子项目）：

```bash
/home/ja/project/RuoYi-Vue3-FastAPI/.venv/bin/python -m pytest tests/plugins/ai/test_knowledge_graph.py tests/plugins/ai/test_knowledge_runtime.py tests/plugins/ai/test_knowledge_controller.py -q
node --test tests/plugins/knowledgeGraphBuild.test.js
npm run build:prod
```
