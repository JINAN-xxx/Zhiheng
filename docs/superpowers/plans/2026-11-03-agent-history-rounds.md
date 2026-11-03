# 智能体上下文轮数实现计划

> **面向 AI 代理的工作者：** 使用 superpowers:subagent-driven-development 逐任务实现此计划。

**目标：** 智能体开发者可选择 0～20 轮历史，保存后调试生效，发布后对外调用生效。

**架构：** 轮数属于 AgentConfig（JSON 配置），默认 20，旧草稿及旧发布版本读取时补默认值，无数据库迁移。应用层在组装可信提示词、技能与知识上下文前裁剪历史；工作空间采用其已有发布快照的相同配置。普通对话 30/15 摘要策略不变。

**技术栈：** Pydantic、FastAPI、PostgreSQL、LangChain 1.4.7、Vue 3、Ant Design Vue。

## 任务 1：配置、执行和界面

文件职责：

- `backend/src/zhiheng/modules/agents/contracts.py`：`history_rounds: int = Field(default=20, strict=True, ge=0, le=20)`。
- 智能体模块新增纯历史选择函数，经 `public.py` 导出；使用用户消息边界选择最近 N 轮及当前问题，禁止截断半轮；兼容既有前导 assistant 历史。
- `backend/src/zhiheng/application/agent_runs.py`：调试取草稿，API 取发布配置；原始请求继续参与幂等指纹，Run 保存裁剪后的实际上下文。
- `backend/src/zhiheng/application/work_runs.py`：按工作成员发布快照裁剪共享历史，再加入任务目标及可信系统提示词。
- `frontend/src/modules/agents/api/index.ts`、`presentation.ts`：解析、表单默认值及序列化，空输入不能默默变成 0。
- `frontend/src/modules/agents/components/AgentEditorModal.vue`：现有模型配置分区增加“上下文轮数”输入，范围 0～20，整数，说明保存后调试生效、发布后 API 生效。
- 调试请求只发送所选历史范围，但完整消息仍在界面显示；失败回复不成为完整历史，重试意图仍保持幂等。
- 测试放现有 agents 单元、集成、前端 core/browser 文件附近，覆盖默认值、0/1/20、越界/布尔/小数、完整轮选择、保存和发布隔离、实际 Provider 请求、知识/技能保留与重放。

- [x] 先写测试并运行，确认缺失功能失败。
- [x] 实现上述最小变更，运行定向测试。
- [x] 文档说明轮数语义，20 轮上限不会限制可见聊天历史，也不会增加智能体自动摘要功能。
- [x] 独立规格审查通过后进行代码质量审查，修复具体问题并复验。

示例断言：

```python
assert AgentConfig(name='助手', model_id=model_id).history_rounds == 20
assert [m.content for m in select_agent_history(messages, 0)] == ['当前问题']
assert [m.content for m in select_agent_history(messages, 1)] == ['最近问题', '最近回答', '当前问题']
```

验证命令（使用现有 SDK 与私有 PostgreSQL/Redis）：

```bash
python -m pytest tests/core/unit/test_agent_history.py tests/core/integration/test_agents.py
node --experimental-strip-types --test tests/core/agentIntent.test.ts
npm run typecheck
npm run lint:core
```

## 任务 2：运行验证与同步

- [x] 私有环境实际模型请求验证 0/1/20 轮；不调用正式收费模型。
- [x] 浏览器验证保存、重载、调试、发布及历史仍可见。
- [x] 定向测试、格式、架构约束通过；不执行前端构建和 SDK 升级。
- [x] 将完成的提交快进同步到 VS Code 根目录；核对原有未提交文件保持不变。
- [x] 控制重启 API/Worker，检查服务就绪与实际字段，再移除任务自己创建的测试资源。
