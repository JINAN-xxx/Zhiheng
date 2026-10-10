# 智衡后端代码导航

本目录包含 FastAPI 应用、管理后台、AI 与智能体插件、知识库、命令行工具及数据库脚本。后端按模块化单体组织，已通过插件公开接口收拢模型、应用、费率、开放 API 和持久资源引用等协作边界。

第一次阅读建议从 **[后端目录说明](docs/后端目录说明.md)** 开始。该说明按当前目录的实际代码整理，包含架构图、启动流程、各目录职责和按功能查找代码的索引。

> 更新日期：2026-10-10，对应 `release/platform-integration-merge` 集成版本。两个本地工作区仍是独立目录，源码、实际运行镜像和业务数据库分别管理，不能仅凭目录名称判断部署版本。

常用文档：

- [后端目录说明](docs/后端目录说明.md)：整个后端如何组织、各功能在哪里。
- [项目代码结构](../代码结构说明.md)：前后端、部署和数据流总览。
- [模块边界说明](../docs/architecture/2026-09-29-module-boundaries.md)：公开接口、引用保护和改造结果。
- [CLI 使用说明](docs/cli_usage.md)：`zhiheng` 命令、交互终端与运维操作。
- [插件开发说明](docs/plugin_development.md)：插件清单、路由、权限与生命周期。
- [文件管理说明](docs/file_management_usage_guide.md)：上传、访问控制与文件生命周期。
- [传输加密配置](docs/transport_crypto_config.md)：请求加解密配置。
- [插件迁移故障处理](docs/plugin_migration_failure_runbook.md)：迁移失败的诊断与恢复。
- [知识库集成说明](module_Knowledge/README.md)：文档解析、Milvus 与知识图谱依赖。
- [项目部署说明](../deploy/README.md)：从仓库根目录部署完整平台。
- [镜像交付与回滚](../deploy/release/README.md)：构建离线包、部署与兼容版本回滚。
