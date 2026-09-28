# 聊天默认显示调整

聊天页不再显示空闲时的“就绪”标记。深度思考过程默认折叠，适用于新回复和历史消息，用户仍可点击展开、收起。

本次仅更新前端静态产物，运维入口为 `data/agent-releases/20260917-chat-display-defaults/manage.sh`。后端沿用 `model-connection-fix-20260917`，只重建前端容器，部署前无活动聊天调用。后续前端更新见 [历史列表样式统一](2026-09-17-history-ui-alignment.md)。

前端 43 组现有插件测试及生产构建通过。使用生产构建和隔离接口验证“就绪”不显示、新回复及历史消息默认折叠、手动展开收起正常。公网部署也完成相同验证。首页 SHA-256 为 `30748b9aa1b893387bba74784390ced9eac539bc85e9eace10fe9b9880a7276e`。

回退前端可使用 `data/agent-releases/20260917-model-connection-fix/manage.sh up -d --no-deps zhiheng-frontend`。
