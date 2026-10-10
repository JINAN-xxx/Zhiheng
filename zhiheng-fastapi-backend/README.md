# 智衡后端代码导航

本目录包含 FastAPI 应用、管理后台、AI 与智能体插件、知识库、命令行工具及数据库脚本。

第一次阅读建议从 **[后端目录说明](docs/后端目录说明.md)** 开始。该说明按当前目录的实际代码整理，包含架构图、启动流程、各目录职责和按功能查找代码的索引。

> 本文档所在工作区是 `/home/ja/project/RuoYi-Vue3-FastAPI`，与 `/home/ja/project/zhiheng` 是两个独立目录。说明不代表另一个工作区或在线容器的最新实现。

常用文档：

- [后端目录说明](docs/后端目录说明.md)：整个后端如何组织、各功能在哪里。
- [CLI 使用说明](docs/cli_usage.md)：`zhiheng` 命令、交互终端与运维操作。
- [插件开发说明](docs/plugin_development.md)：插件清单、路由、权限与生命周期。
- [文件管理说明](docs/file_management_usage_guide.md)：上传、访问控制与文件生命周期。
- [传输加密配置](docs/transport_crypto_config.md)：请求加解密配置。
- [插件迁移故障处理](docs/plugin_migration_failure_runbook.md)：迁移失败的诊断与恢复。
- [知识库集成说明](module_Knowledge/README.md)：文档解析、Milvus 与知识图谱依赖。
- [项目部署说明](../deploy/README.md)：从仓库根目录部署完整平台。
