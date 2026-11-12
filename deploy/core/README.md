# Docker Compose 配置

本目录提供平台镜像部署与源码开发的 Compose 配置。

- [快速开始](../../README.md#快速启动)
- [完整部署、升级与备份指南](../../docs/deployment.md)
- [环境配置示例](.env.core.example)
- compose.yaml：数据库、Redis、Milvus、迁移、API、Worker 和网页。
- compose.source.yaml：源码挂载与前端热更新覆盖配置。

从仓库根目录使用 deploy.sh 管理镜像部署，使用 dev.sh 管理源码模式。真实 .env.core 配置由初始化脚本生成，不进入 Git。
