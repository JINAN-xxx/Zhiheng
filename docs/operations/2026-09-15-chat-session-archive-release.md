# 普通聊天归档发布

同日后续已修正三点菜单布局，当前前端挂载与运维入口以 [菜单布局发布记录](2026-09-15-chat-menu-polish.md) 为准。本文保留归档功能和后端发布定位。

用户反馈普通聊天历史列表缺少归档入口。检查确认：功能源码已经合入，但 `12580` 仍挂载 UI polish 版本，运行中的后端也未注册会话状态接口。

2026-09-15 已更新前后端应用容器，普通聊天侧栏现在提供“最近会话 / 已归档”切换。鼠标移到历史会话行，点击右侧“更多操作”，可以选择“归档会话”；归档记录可查看历史、恢复后继续聊天。

## 发布定位

- 源码提交：`03b781b3`，发布基于包含验证记录的 `ad0adabb`。
- 后端镜像：`ruoyi-backend-pg:chat-session-archive-20260915`。
- 镜像 ID：`sha256:cc824c33a23f8109b5a2507f53dc8101cb18dfc2be92971c3873961a08f94445`。
- 发布目录：`data/agent-releases/20260915-chat-session-archive/`。
- 前端：发布目录中的 `frontend/`，包含 5 份 Monaco worker。
- 首页 SHA-256：`81ff77f937820f4878a48ce378ce901f4bba49a1885c4cb3c4f90f2ab4e2de89`。

后端镜像基于当前运行镜像，只覆盖归档功能涉及的 4 个 Python 源文件；构建前逐文件确认运行代码与归档前源码一致。Compose 渲染后逐项确认应用环境变量与持久化挂载匹配当前服务。

沿用 PostgreSQL 与已有 status 字段，无数据库迁移。此次只更新 `ruoyi-backend-pg`、`ruoyi-frontend` 两个应用容器。

## 验证

- `12580` 首页字节与发布产物一致，实际 `/docker-api/openapi.json` 已包含 `PATCH /ai/chat/session/{session_id}/status`。
- 后端处于 healthy 状态。
- 真实登录会话下，桌面 1440px 与手机 390px 均确认普通聊天历史菜单中的“归档会话”可见，“已归档”列表实际请求成功，无页面异常和横向溢出。
- 浏览器没有拦截或模拟业务 API，没有归档、恢复或删除用户聊天。临时独立登录会话已撤销，私有令牌文件已删除。
- 功能开发阶段后端 96 项、前端 33 组测试通过，生产构建及归档/恢复浏览器流程通过，详见对应实现计划。

只读页面验收脚本、截图与无密钥摘要位于发布目录的 `probes/`、`screenshots/` 和 `live-ui-summary.json`，不提交 Git。

## 运维与回退

日常运维使用发布目录中的 `manage.sh`，它固定已有 Compose 配置组合，并追加本次 `compose.override.yml`。环境文件继续使用 `20260914-chat-work-dual-mode/private/compose.env`。

若需回退，使用此前的 Compose 三文件组合（`antd-minimal-loop/docker-compose.pg.yml`、`20260914-chat-work-dual-mode/compose.override.yml`、`20260915-conversation-ui-polish/compose.override.yml`），仅重建两个应用容器。此前镜像和静态产物保留，不恢复或覆盖数据库。
