# 知识图谱与已有知识库补建

提交 `274b85f4` 已发布到公网 `http://115.190.121.37:8060/`。本版运维入口为 `data/agent-releases/20260917-knowledge-graph/manage.sh`；后续后台更新见 [模型接入自动授权修复](2026-09-17-model-runtime-access.md)。

## 使用方式

- 新建知识库：勾选“同时构建知识图谱”，选择已发布的对话模型；上传文档后后台构建。
- 已有知识库：进入“知识图谱”，选择对话模型并点击“启用并构建图谱”。页面显示进度、当前文档、失败原因，支持补建及重试。
- 只读用户可以查看图谱和进度，启动构建需要 `ai:knowledge:edit`。构建期间暂不修改文档，向量检索继续可用。
- 图谱状态与向量索引状态独立；图谱失败保留已完成的向量索引。多文件先整批上传、再集中索引，单批最多 20 个。
- 服务关闭或重启后，未完成任务可按 interrupted 状态重试；查询不会隐式调用模型补建。Embedding 绑定或维度变化会拒绝向旧索引写入。

原线上只接入了可选图谱代码，缺少依赖和图存储配置，`graph_available=false`。发布后确认该值为 true。当前用户数据仍为 1 个 Milvus 知识库、3 个文档，尚未启用图谱；本次提供补建入口，没有自动处理这些文档。

## 存储与运行环境

向量继续使用 Milvus；图关系、图谱 KV 和文档状态使用现有 PostgreSQL。LightRAG 固定为 1.5.7，使用 `PGTableGraphStorage`、`PGKVStorage`、`PGDocStatusStorage`，不需要 AGE 扩展。实现参考 [固定版本 PG 图存储源码](https://github.com/HKUDS/LightRAG/blob/v1.5.7/lightrag/kg/pgtable_impl.py)。

- 复用平台 DB_* 连接配置和 KNOWLEDGE_MILVUS_* 配置，凭据不写入知识库元数据或前端。
- 保持一个后端 worker。每个知识库使用独立 workspace；禁止全局 POSTGRES_WORKSPACE、MILVUS_WORKSPACE 覆盖。
- 本次没有新增数据库服务或对外端口。LightRAG 初始化自己的 lightrag_* 表；初始化前已确认这些表不存在。
- 固定并保留原镜像全部 185 个依赖版本，只增加 11 个依赖，`pip check` 通过。依赖 wheel 和约束记录在发布目录 `dependency-image/`。
- 后端镜像为 `zhiheng-backend-pg:knowledge-graph-20260917`，基于 `knowledge-graph-deps-20260917` 增量复制本次代码。
- 镜像预置校验 SHA-256 的 o200k 分词数据，TIKTOKEN_CACHE_DIR 为 `/app/tokenizer-cache`。资源缺失或损坏立即报错，不在请求中联网下载；加载在线程中完成。
- Milvus 首次集合初始化在线程中执行，保留 SDK 初始化锁。连续取消及初始化超时均等待线程结束后再清理连接，已在 Python 3.10 验证。

正常源代码 Docker 构建会运行 `scripts/prepare_graph_tokenizer.py`。若容器网络无法下载资源，可先在宿主机按模块 README 准备 `tokenizer-cache/`，或使用脚本的 `--source-cache` 离线复制模式；资源目录已排除 Git 跟踪。

## 验证

- 最终知识库相关后端测试 **105 passed、2 skipped**；跳过项为可选真实存储测试，另行执行了下述实际 PG/Milvus 验证。前端插件测试 **41 组通过**；Ruff、diff 检查及构建通过。
- 独立规格、质量审查通过。覆盖取消清理竞态、Embedding 绑定变化、图谱问答不占向量锁、空库清理、永久删除 Milvus 集合、批量上传及自动进度刷新。
- PostgreSQL 原生图存储插入 2 个节点和 1 条边，关闭重开后仍可读取，测试 workspace 已清理。
- 使用独立测试知识库、真实平台模型和真实 PG/Milvus，完整执行“先建向量索引→启用已有库图谱→补建→图谱检索→关闭重开→删除文档和库”。得到 **6 个实体、6 条关系**，图谱检索返回非空上下文；主向量 chunks 未变化，图谱持久化正常。
- 最新实链路中，构建期间向量查询在约 4.235 秒返回，事件循环采样最大间隔为 0.209 秒。线程化前首次初始化曾阻塞约 7.225 秒。该结果是一次实测，SDK 其他同步 RPC 和故障时的等待仍不构成硬性延迟保证。
- 测试空间的 PostgreSQL 图节点和 Milvus 集合均已清理。用户知识库及文档数量核对一致。
- 本机最终构建通过原知识库、图谱功能、批量上传 3 份桌面浏览器回归。浏览器业务 API 使用隔离 fixture；真实模型验证使用上文独立知识库，没有绕过生产登录或验证码。
- 线上确认 graph_available=true，存储选项仍只有 Milvus；公网 OpenAPI 包含 graph-build 的 GET/POST，Nginx 语法正常、后端 healthy、FRP active。
- 公网真实验证码加载、刷新、故意错误验证码的加密登录校验通过，脚本错误为零。
- 公网和本机首页一致，SHA-256 为 `9f4692c6f991e924ee134dc17711b82f08addb147813ed4bb60a0bf26d05d85f`。

公网组合浏览器回归中的首次页面加载仍出现过超时，和之前发布记录中的现象一致；本次图谱启用/进度/权限场景通过，新建及文档状态场景独立浏览器复核通过。加入诊断的文档场景无脚本、控制台或网络请求错误。该间歇性现象不能据此认定已经修复。

## 部署与数据保留

Compose 组合只替换后端镜像和前端静态目录。Nginx 配置沿用附件版本；FRP 8060、本机 12580、后端 `127.0.0.1:19099` 和知识库、技能、私有文件挂载均保留。发布前确认没有 pending/running 聊天或工作请求，仅重建两个应用容器。

用户知识库元数据备份位于发布目录 `private/metadata-before.json`（0600）。实链路测试的一次性环境文件已清理。

回退时必须保留知识库元数据、PostgreSQL 图表和 Milvus 数据。旧后端使用不同图存储适配器，不能直接读取本次 PostgreSQL 图谱；已有启用的知识库应保留本版数据层，先停止构建并备份状态，再处理兼容回退。不要通过删除图谱表或集合回退应用。
