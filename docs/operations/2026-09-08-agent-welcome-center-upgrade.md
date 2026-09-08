# 智能体欢迎语与居中弹窗发布记录

已将智能体扩展升级到 `0.2.1`，入口仍为 `http://127.0.0.1:12580/extensions/agent`。

- 新增可选 `welcomeMessage`，最长 2000 字符，旧记录及旧发布快照默认为空。
- 欢迎语在试聊开场展示，清空对话后恢复，不进入模型消息历史；保存草稿和发布快照保持隔离。
- 新建、编辑配置使用居中弹窗，桌面采用双列基础字段，手机使用单列；正文可滚动，操作区保持可见。
- PostgreSQL `003_agent_welcome_message.sql` 执行成功，历史迁移校验一致并跳过。已有配置通过默认空字符串兼容。

验证：197 项后端回归、28 组前端插件测试、Ruff、Docker 模式构建通过。模拟浏览器流程完成 82 次请求；真实页面验证保存、发布、欢迎语显示/清空/恢复、发布隔离和桌面/手机居中，未出现接口或页面错误。真实模型 `deepseek-v4-flash` 在试聊中返回 `AGENT_LIVE_OK`。临时智能体、应用及后台会话已清理。

本机发布目录：`data/agent-releases/20260908-agent-extension-0.2.1/`。后端基于已验证的 `0.2.0` 镜像构建并包含当前完整后端源码，镜像 ID 为 `sha256:9d0ac52891d43f9fbfc0d9b2247b316b4794d040767db7881f7115d74493ea8a`。前端挂载本发布目录的 `frontend/`。

数据库升级前备份位于 `private/ruoyi-before-upgrade.dump`，已验证可读取。17 项部署环境值与升级前一致，配置保存在受限的 `private/compose.env`。临时会话令牌已从 `private/probe-state.json` 清理，真实浏览器结果见 `welcome-browser-result.json`。

当前发布的服务命令：

```bash
docker compose \
  --project-directory /home/ja/project/RuoYi-Vue3-FastAPI/.worktrees/antd-minimal-loop \
  --env-file /home/ja/project/RuoYi-Vue3-FastAPI/data/agent-releases/20260908-agent-extension-0.2.1/private/compose.env \
  -p ruoyi-vue3-fastapi \
  -f /home/ja/project/RuoYi-Vue3-FastAPI/.worktrees/antd-minimal-loop/docker-compose.pg.yml \
  -f /home/ja/project/RuoYi-Vue3-FastAPI/data/agent-releases/20260908-agent-extension-0.2.1/compose.override.yml \
  up -d --no-deps --no-build ruoyi-backend-pg ruoyi-frontend
```

真实截图：`/tmp/agent-welcome-live-editor.png`、`/tmp/agent-welcome-live-chat.png`、`/tmp/agent-welcome-live-mobile.png`。
