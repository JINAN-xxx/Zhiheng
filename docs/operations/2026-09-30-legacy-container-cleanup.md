# 旧容器清理记录

日期：2026-09-30。用户要求优先清理旧容器，并明确存储不足，不再追加镜像导出包。

已核对并移除以下 7 个处于 exited 状态的容器，按保存的完整容器 ID 执行删除，未使用强制删除或卷删除选项：

- `ruoyi-frontend`
- `ruoyi-backend-pg`
- `ruoyi-milvus`
- `ruoyi-redis`
- `litellm-proxy`
- `litellm-redis`
- `litellm-db`

删除前检查了镜像 ID、挂载和可写层差异。业务数据库、Redis、知识库、技能与附件使用独立卷或宿主目录；没有发现需另行保存的后端业务文件写入未挂载路径。删除后再次验证原镜像和以下三个 Docker 卷仍存在：

- `litellm-postgres-data`
- `litellm-redis-data`
- 原 `ruoyi-redis` 匿名卷 `72782308f97259eb431e4d3a7fc6088abf4799404f13cc78da237b0c75425222`

`/home/ja/project/RuoYi-Vue3-FastAPI` 下原有挂载目录未清理，包含知识库、Milvus、附件和旧工作树中的技能文件。旧业务数据没有因此迁移到新数据库。不要将这些卷和目录作为无用数据进行 prune 或删除。

恢复元数据保存在 `/home/ja/.local/state/zhiheng-legacy-backups/20260930-_eatyljv/`：`inventory.json`、`volumes.json`、`writable-changes.json`、`cleanup-result.json`。`containers.private.json` 包含完整容器环境与配置，文件权限为 0600，目录权限为 0700。此处只有小体积配置记录，不是数据库备份，也没有新增旧镜像 tar 包。

另清理了本次验证目录中 `package-rc2` 的重复镜像归档，先释放约 5.91 GiB；验收完成后清理临时安装目录，重复归档合计释放约 11.81 GiB，最新完整包保留为 `package-rc4`。临时演练容器、测试卷及数据均已清理，当前保留 7 个健康的 `zhiheng` 常驻服务，以及已成功退出的一次性 `zhiheng-platform-init-1` 任务。发布验收详见[发布与回滚记录](./2026-09-30-release-delivery.md)。
