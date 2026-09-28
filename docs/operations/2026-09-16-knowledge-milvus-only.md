# 知识库固定使用 Milvus

后续已发布智能对话附件功能，当前运维组合以 [对话附件记录](2026-09-16-chat-attachments.md) 为准；本次的 Milvus 限制继续生效。

按用户要求，新建知识库默认且仅允许 Milvus。创建表单展示固定存储类型，移除存储类型下拉框及列表中的类型筛选。

## 实现

- 代码提交：`66d4dfde`。
- 后端 `DatabaseCreate.kb_type` 为 `Literal['milvus'] = 'milvus'`，其他类型在请求校验阶段拒绝。
- `/ai/knowledge/types` 只公开 Milvus，`graph_available` 独立表示图谱能力。图谱继续作为可选功能。
- 前端创建 payload 固定为 Milvus；Milvus 配置不满足时显示具体提示并禁用创建。
- 既有知识库按其元数据继续读取，未改写存储类型、删除历史适配器或迁移向量。检查开始时现有 1 个 Milvus 库、2 个文档；期间新增 1 个文档，发布后 3 个文档均为 `done`。

## 发布定位

当前运维入口：`data/agent-releases/20260916-knowledge-milvus-only/manage.sh`。它组合上一版 `20260916-captcha-http/manage.sh`，只更新应用镜像和前端静态挂载。

- 后端镜像：`zhiheng-backend-pg:knowledge-milvus-only-20260916`。
- 镜像以此前运行的 `zhiheng-backend-pg:chat-session-archive-20260915` 为基础，仅复制经过验证的 `models.py`、`runtime.py`；基线文件与旧容器逐字一致，构建输入和 Dockerfile 保存在发布目录的 `backend/`。
- 静态文件：发布目录内 `frontend/`，5 个 Monaco worker 齐全。
- Nginx 配置沿用 `20260916-nginx-entrypoint/nginx.conf`，FRP 公网 8060、本机 12580、后端仅绑定 `127.0.0.1:19099`。
- Compose 差异校验确认只改变后端镜像和前端静态挂载。发布前检查无 pending/running 工作消息，仅重建应用容器。

## 验证

- 后端知识库相关测试：30 通过，2 跳过（可选真实存储集成测试）。Ruff 和 diff 检查通过。
- 前端插件测试：34 组通过。Docker 模式构建成功，保留既有大文件分块提示。
- 独立代码审查未发现阻塞问题。
- 桌面浏览器检查覆盖固定 Milvus 创建请求、无类型下拉框和筛选、存储不可用时禁止提交、创建失败重试、文档管理、检索和图谱页面。浏览器业务请求使用隔离 fixture，未创建实际知识库。
- 公网发布后复测以上桌面交互以及仅查询权限的深链。组合检查中深链曾触发页面元素等待超时，断言等待统一为 20 秒后仍出现过；加入网络与脚本诊断后完整顺序复跑通过，静态资源均为 200、无失败请求或脚本异常。此间歇性加载问题尚未稳定定位，不能据此认定之前用户报告的“接口失败”已修复。未修改产品请求超时。
- 在运行容器中确认 Milvus 实际连接成功，1 个 collection；类型能力只有可用 Milvus，图谱能力为 false。现有 1 个知识库和 3 个已完成索引的文档正常。
- 真实公网 OpenAPI 创建模型的 `kb_type` 同时为 `const=milvus`、`default=milvus`。公网与本机首页相同，SHA-256 为 `3fef1d7d2581fde13ac1ad51faa74d9c824acebde5e72edea21d5536d6819a5d`。
- 公网 HTTP 验证码真实接口回归通过：图片 160×60、页面重载、手动刷新、故意错误验证码的加密登录校验均正常，脚本错误为零。
- Nginx 语法通过、后端 healthy、FRP active。

复测桌面页面：

```bash
cd zhiheng-fastapi-frontend
KNOWLEDGE_CHECK_BASE_URL=http://115.190.121.37:8060 KNOWLEDGE_CHECK_DESKTOP_ONLY=1 ../.venv/bin/python tests/e2e/knowledge_check.py
CAPTCHA_CHECK_URL=http://115.190.121.37:8060 ../.venv/bin/python tests/e2e/captcha_transport_check.py
```

## 回退

使用上一版应用组合恢复镜像和页面，无数据库迁移：

```bash
data/agent-releases/20260916-captcha-http/manage.sh up -d --no-deps --no-build zhiheng-backend-pg zhiheng-frontend
```
