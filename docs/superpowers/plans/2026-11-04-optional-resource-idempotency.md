# 资源接口可选幂等键实现计划

> **面向 AI 代理的工作者：** 使用 superpowers:subagent-driven-development，先测试再实现，规格审查通过后再做质量审查。

**目标：** 12 个资源操作允许省略 `Idempotency-Key`；提供键时保留既有可靠重放与授权保障。

**架构：** 在 API 公共层解析可选请求头并输出有效 UUID。缺少请求头时，每次 HTTP 请求生成新内部编号；业务层继续强制 UUID，沿用原 APIRequest、原生任务和计费账本。没有键的两个请求不承诺去重。

**技术栈：** FastAPI、Pydantic、PostgreSQL、原生资源用例、Vue 3。复用现有依赖与测试环境，不迁移数据库、不构建前端。

## 实现与定向验证

- [x] 在现有测试附近添加公共解析器测试及资源 HTTP 用例，先确认省略键失败。断言：省略可用且内部编号不同；提供同一键返回原结果；更改内容 409；空值、非法 UUID、重复头仍 422。
- [x] `backend/src/zhiheng/api/common/preconditions.py` 集中解析，避免各路由分散生成 UUID；不能用内容哈希自动合并两次业务操作，也不能将 HTTP 追踪 ID 当作跨请求幂等保证。
- [x] `backend/src/zhiheng/api/resources/{files,knowledge,skills}.py` 接入公共解析规则，共 12 个操作。模型与智能体 `/v1`、控制台协议及 If-Match 规则沿用现有实现。
- [x] `backend/src/zhiheng/api/common/openapi.py` 将幂等头标成可选并说明两种调用语义；`examples.py` 默认提供不含幂等键的最简 curl，另给可靠重试方式，命名 `IDEMPOTENCY_KEY`，保留 DTO 校验和分组完整性。
- [x] `frontend/src/modules/developer/components/ApplicationApiDocs.vue` 与 `docs/api/resources-v1.md` 同步说明：API Key 鉴权、服务端资源 ID、可选幂等键、服务端追踪 ID 分别承担不同职责。前端现有业务重试意图沿用，不新增 SDK 工程。
- [x] 运行相关资源文件、知识库、技能、请求身份、操作及 OpenAPI 回归，核对授权撤销、并发去重、快照过期和原生任务/账本计量；增加省略键覆盖，维持当前业务归属。
- [x] 执行前端 core/类型/格式、后端 Ruff/mypy/架构检查；独立规格和质量审查。

示例检查：

```python
assert first_without_key.status_code == 201
assert second_without_key.status_code == 201
assert first_without_key.json()['id'] != second_without_key.json()['id']
assert replay_with_same_key.json()['id'] == original_with_key.json()['id']
```

## 当前实例同步

- [x] 私有本机环境完成创建知识库、上传、入库/操作查询、技能导入的实际调用；保留原有传键客户端验证。
- [x] 正常平台无在途任务时将验证后的提交同步回 VS Code 根目录，API 源码重载并核对 12 个头均可选、269 条实际路径仍一致。
- [x] 保留原有未提交内容、数据、密钥与费率；清理任务自建资源和临时依赖链接，保留验证证据。
