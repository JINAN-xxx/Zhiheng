# 智能体扩展 0.2.0 升级与真实联调

后续已发布 `0.2.1`，当前运行命令见 [欢迎语与居中弹窗发布记录](2026-09-08-agent-welcome-center-upgrade.md)。以下保留 `0.2.0` 的历史升级记录。

已将当前分支的智能体功能部署到原 Docker 环境。入口为 `http://127.0.0.1:12580/extensions/agent`，后端映射端口仍为 `19099`。

## 发布结果

- `agent_extension` 已由 `0.1.0` 升级为 `0.2.0`，保持启用。
- PostgreSQL 的 `002_agent_management.sql` 执行成功；已执行的技能迁移按校验值跳过。
- 新增智能体管理菜单与权限，实际路由组件为 `plugin/agent_extension/agent/index`。
- 后端镜像：`ruoyi-backend-pg:agent-extension-0.2.0-20260908`。
- 后端镜像 ID：`sha256:83797416ad36f91be2c5edb83d39995e2df9466ca8ab9732824f2fd1326f8ef8`。
- 前端按 Docker 模式构建，使用 `/docker-api`，已切换到独立发布目录。
- 17 项 Compose 环境配置与升级前容器一致。现有模型、API 密钥配置和技能存储挂载沿用原配置。

升级时发现旧容器的 AI 源码曾在镜像构建后更新，基础镜像中的版本较旧。最终镜像已覆盖当前完整后端源码，并在 Python 3.10 容器内验证管理、试聊和公开路由可以正常导入；后端健康检查已恢复。

## 真实联调

使用现有模型 `deepseek-v4-flash`，绑定已安装的 `document-summary` 技能，知识库绑定为空。所有请求经真实 Nginx 和后端执行。

| 检查 | 结果 |
| --- | --- |
| 保存并试聊智能体 | 返回 `AGENT_LIVE_OK`，239 输入 Token、52 输出 Token |
| 发布后 JSON 调用 | 返回 `AGENT_LIVE_OK`，239 输入 Token、45 输出 Token |
| 发布后 SSE 调用 | 返回 `AGENT_LIVE_OK`，8 个数据帧，含 `[DONE]` |
| 用量记录 | 两次公开调用均为 `completed` |
| 无效密钥 | HTTP 401 |
| 调用方覆盖模型配置 | HTTP 400 |
| 取消发布后调用 | HTTP 404 |
| 撤销密钥后调用 | HTTP 401 |
| 真实浏览器页面 | 桌面 1440×900、手机 390×844 均通过，无接口或页面错误 |

临时智能体已删除，临时应用已停用并删除，测试密钥已撤销，短期后台联调会话已清理。用量审计按平台原有规则保留。知识库检索仍由同事按 [接入契约](../agent-knowledge-integration.md) 实现。

## 发布文件与复现

本机发布目录为 `data/agent-releases/20260908-agent-extension-0.2.0/`，该运行目录已忽略 Git 跟踪。

- `frontend/`：当前部署的静态资源。
- `backend/`、`Dockerfile`：完整后端源码快照和构建定义；环境文件及运行数据不从源码目录覆盖。
- `compose.override.yml`：本次镜像、前端资源及前端源码挂载配置。
- `deployment.json`：原镜像及 Compose 命令记录。
- `browser-result.json`：真实浏览器验证结果。
- `private/ruoyi-before-upgrade.dump`：升级前数据库备份，已用 `pg_restore --list` 验证可读。
- `private/compose.env`：从原容器继承的部署配置，仅供本机部署使用；目录权限 0700、文件权限 0600。
- `private/probe-state.json`：已移除会话令牌和明文密钥的联调结果。

后续重启本次发布的服务使用相同项目和覆盖文件：

```bash
docker compose \
  --project-directory /home/ja/project/RuoYi-Vue3-FastAPI/.worktrees/antd-minimal-loop \
  --env-file /home/ja/project/RuoYi-Vue3-FastAPI/data/agent-releases/20260908-agent-extension-0.2.0/private/compose.env \
  -p ruoyi-vue3-fastapi \
  -f /home/ja/project/RuoYi-Vue3-FastAPI/.worktrees/antd-minimal-loop/docker-compose.pg.yml \
  -f /home/ja/project/RuoYi-Vue3-FastAPI/data/agent-releases/20260908-agent-extension-0.2.0/compose.override.yml \
  up -d --no-deps --no-build ruoyi-backend-pg ruoyi-frontend
```

真实页面截图保存在 `/tmp/agent-live-list.png`、`/tmp/agent-live-config.png`、`/tmp/agent-live-api.png` 和 `/tmp/agent-live-mobile.png`。
