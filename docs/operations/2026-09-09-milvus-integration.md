# Milvus 接入

本机已有 Milvus `v2.6.9` 镜像，但未运行容器。本次复用该镜像，以官方单容器部署配置启用内嵌 etcd 与本地持久化存储，独立数据目录为 `data/milvus`。配置依据：[官方 v2.6.9 脚本](https://github.com/milvus-io/milvus/blob/v2.6.9/scripts/standalone_embed.sh)。

实施步骤：

- [x] 校验并启动 `docker-compose.milvus.yml`，确认健康检查与持久化目录。
- [x] 安装独立客户端依赖 `requirements-knowledge-milvus.txt`，设置后端 `KNOWLEDGE_MILVUS_URI=http://zhiheng-milvus:19530`，保留已有模型、密钥和知识库数据。
- [x] 验证真实 Milvus 写入、检索、重新切片、删除与重启后持久化，完成智能体与公开 API 联调。

Milvus 加入已有 `zhiheng-network`；宿主机仅监听 `127.0.0.1:19530`，管理页面为 `http://127.0.0.1:19591/webui/`。平台前端端口仍为 `12580`。内嵌 etcd 不向宿主机发布端口。

本次服务以 4 GiB 内存、2 CPU 运行。此配置用于当前平台联调；随着文档规模增长，应根据索引和实际负载调整资源。已有 Chroma 知识库保持各自存储类型，新知识库可以选择 Milvus。

使用前确保本机已导入配置中指定的镜像，并设置绝对路径 `MILVUS_DATA_DIR`；Compose 的 `pull_policy: never` 保证不会隐式更换镜像。启动命令：

```bash
MILVUS_DATA_DIR=/home/ja/project/ZhiHeng-Vue3-FastAPI/data/milvus \
  docker compose -p zhiheng-knowledge -f docker-compose.milvus.yml up -d
```

备份时同时保留平台 `data/knowledge` 元数据与 Milvus 数据；使用本地存储模式时，应在服务停止或使用一致性快照的条件下备份完整 `data/milvus`。不删除现有容器数据目录进行“修复”。

## 部署与验证结果

实际服务版本经 SDK 查询为 `2.6.9`，容器名 `zhiheng-milvus`。稳定运行配置位于 `data/agent-releases/20260909-milvus/service/`，数据挂载指向主工作区的绝对路径。服务保留 60 秒正常关闭时间。

后端安装 PyMilvus `2.6.17`，AI 插件升级至 `0.9.1`。修复了集合创建中断后的恢复：写入重试先验证索引规格，再补齐缺失的索引和加载；初始化等待上限为 60 秒，普通查询仍为 8 秒，查询不会创建或重建集合。

后端镜像为 `zhiheng-backend-pg:milvus-integration-20260909`，镜像 ID 为 `sha256:38b4820113965f0a5786e3cbf95ef4f542065068e4dd9fa689edd948a67c57ab`。镜像以此前知识库版本为基础，通过离线 wheel 与版本约束保留原有依赖；`pip check` 通过。Compose 配置保留 19 项运行环境设置，其中新增 Milvus 内部连接地址。

发布目录 `private/compose.env` 仅供维护使用，权限为 600；数据库备份 `private/zhiheng-before-milvus.dump` 已通过 `pg_restore --list` 验证。插件升级预检和实际升级均返回成功，历史迁移校验值一致。

验证结果：

- AI、智能体及插件契约回归：1473 项通过，1 项 Vue 2 条件跳过；包含真实 Milvus 缺索引、未加载状态恢复用例。修改的 Python 文件通过 Ruff。
- 实际 Python 3.10 镜像编译通过；真实 Milvus 写入、检索、客户端重新连接、规格变更保护和删除通过。
- 使用已配置的 `qwen3-vl-embedding` 生成 4096 维向量，真实 Markdown 上传、索引、预览、重新切片和检索通过。
- 智能体试聊、普通聊天、公开 JSON/SSE 调用返回了只存在于测试文档中的随机代号。Milvus 容器重建后，原知识库与已发布智能体仍可调用。
- 真实 `12580` 页面在 1440×900 和 390×844 视口中完成检索，无页面错误或横向溢出。
- 页面验证期间曾出现向量调用超时；原问句独立 Embedding 复测耗时 1.71 秒，相同页面复测通过，保留原超时边界。
- 临时集合、知识库、文档、聊天、智能体和应用已清理，测试密钥与独立登录会话已撤销，私有结果文件已去除明文令牌和密钥。

以后可单独运行真实存储恢复测试（只创建并清理唯一名称的临时集合）：

```bash
KNOWLEDGE_MILVUS_TEST_URI=http://127.0.0.1:19530 \
  python -m pytest tests/plugins/ai/test_knowledge_stores.py -k real_milvus -q
```
