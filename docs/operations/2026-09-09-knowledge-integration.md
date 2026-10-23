# 知识库整合与升级记录

本次以 `release/platform-integration` 的 `3dd71ac` 为基础，选择整合 `release/antd-minimal-loop` 的知识库能力，来源固定为 `f9762470bce611e4db72988d19becc6ce501bb5e`。该来源是无父提交的快照，且部分公共文件存在编码、换行损坏，因此通过选择性整合保留平台现有提交历史。来源快照另有本地引用 `backup/knowledge-source-20260908`。

AI 插件升级至 `0.9.0`，智能体扩展升级至 `0.3.0`。新增 `/ai/knowledge` 原生管理页面，支持知识库、目录、文件上传、索引、预览、重新切片、检索与可选图谱。智能对话和智能体编辑器均可选择知识库，智能体试聊及公开 JSON/SSE 调用使用真实文档引用。聊天消息新增 `knowledge_base_ids` 字段，数据库通过两种方言的 `009_chat_knowledge.sql` 增量迁移。

权限包括 `ai:knowledge:list/query/add/edit/remove`。知识库按平台共享资源管理，智能体检索会重新检查所属用户的当前权限。模型和凭据复用模型中心，知识库元数据仅保存模型 ID、索引规格和文档信息。

默认部署使用 Chroma，安装 `backend/requirements-knowledge.txt`；上传和索引数据通过 `KNOWLEDGE_DATA_DIR=/app/data/knowledge` 持久化到宿主机 `data/knowledge`。向量模型绑定或维度变化时返回需要重建的错误，不在检索过程中清空集合。

Milvus、LightRAG 和 Neo4j 通过可选依赖 `requirements-knowledge-graph.txt` 启用，配置见 `module_Knowledge/README.md`。图谱要求 `APP_WORKERS=1`。当前运行环境未配置图谱基础设施，图谱实链路尚未验证；图谱页面交互及异常边界使用测试夹具验证。

升级产物保存在 `data/agent-releases/20260909-knowledge-integration/`，后端镜像为 `zhiheng-backend-pg:knowledge-integration-20260909`，镜像 ID 为 `sha256:c6323e5d3cc2401c285eebb205756f430d825a2913839d247b8159c5f46a57e7`。基础镜像是此前运行正常的 `agent-extension-0.2.1-20260908`。构建复制完整后端源码，排除环境文件、运行数据、日志和缓存。因容器网络下载超时，最终使用 `offline-image/` 中的离线 wheel 包构建，并通过版本约束保留原镜像已有依赖。发布目录中的 `private/compose.env` 保留现有运行密钥配置，权限为 600；该目录不提交 Git。

部署沿用 Compose 项目 `zhiheng-vue3-fastapi` 和 `.worktrees/antd-minimal-loop/docker-compose.pg.yml`，叠加本次 `compose.override.yml`，必须显式使用本次 `private/compose.env`。前端仍为 `12580`，后端为 `19099`。插件维护用 `/frontend` 挂载主工作区的前端源码，避免依赖临时整合工作区。插件 CLI 使用同一数据库的临时维护容器依次升级 `ai` 和 `agent_extension`；先预检并升级 AI，再预检并升级依赖它的智能体扩展。

已完成的验证：

- AI、智能体和插件声明后端回归：1466 项通过，1 项 Vue 2 条件跳过。测试环境在 `/tmp` 补齐 Chroma、解析器和 fakeredis Lua 依赖。
- 前端 29 个插件测试套件通过；知识库桌面/手机页面、智能体知识库选择、原智能体完整发布流程的浏览器测试通过。
- 前端生产构建通过。真实平台 `qwen3-vl-embedding` 调用返回 4096 维向量。
- 最终后端源码和知识库测试通过 Ruff；部署镜像在 Python 3.10 中编译通过，`pip check` 无依赖冲突。
- 数据库备份 `private/zhiheng-before-knowledge.dump` 已创建并通过 `pg_restore --list` 验证；插件升级返回 `ok: true`，后端容器健康。
- 通过真实 Nginx 接口上传 Markdown 文档、索引、预览、检索和重新切片成功。文档中的随机项目代号被智能体试聊、公开 JSON/SSE 以及普通聊天正确引用，问题和系统提示词没有包含该代号。
- 未登录知识库请求与非法上传路径被拒绝。后端容器重建后，同一知识库检索和已发布智能体调用仍然成功。
- 真实 `12580` 页面在 1440×900 与 390×844 视口中通过检索验证，没有页面错误和页面横向溢出。
- 检索模式选择仅在图谱查询中展示，避免将图谱模式误用于向量检索。最终静态产物为发布目录下的 `frontend-final/`。
- 测试知识库、文档、聊天会话、智能体和应用已清理，测试密钥已撤销，独立测试登录会话已删除。私有结果文件已去除令牌和明文密钥。

代码整合提交为 `92ba5da152d9ee205610e30bf9903a36c5cc0d87`，已快进合并并推送到 `release/platform-integration`。远端 `release/antd-minimal-loop` 未改写；本次不携带来源快照中的环境文件、运行数据或无关平台回退。
