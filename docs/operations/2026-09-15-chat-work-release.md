# 聊天 / 工作正式环境启动记录

同日后续已将前端导航收回“智能对话”内部，并更新静态挂载；当前前端定位以 [导航修正发布记录](2026-09-15-conversation-navigation.md) 为准。本文保留初次后端和数据库发布证据。

2026-09-15，按用户“合并回去、启动”的授权，已将聊天 / 工作版本部署到现有 Docker 服务。前端端口为 `12580`，后端端口为 `19099`；前端通过 `/docker-api` 访问后端。

## 发布版本

- 本地发布分支：`release/platform-integration`，已快进合并 `feat/chat-work-dual-mode`。
- 部署代码：`df38d7a9ea19833e7a6d5308ae9f38ed23aa667d`；最终产品源码提交为 `5e6ad6be`。
- 后端镜像：`ruoyi-backend-pg:chat-work-dual-mode-20260914`。
- 镜像 ID：`sha256:991c4cef07180802a9e658171c673440ae8dc63ffd15a3c8fcb86c4429329056`。
- 发布目录：`data/agent-releases/20260914-chat-work-dual-mode/`，保留准备阶段日期。
- 前端静态文件：上述目录的 `frontend/`；包含 5 份 Monaco worker 及 gzip 文件。
- 首页 SHA-256：`8281890facc6458171953f5f010398dc5feddee0c23209e6372d6a0f029b5976`；实际 `12580` 返回内容与发布文件一致。

后端保留现有依赖镜像、`--env=dockerpg` 启动参数、运行环境和数据挂载。Nginx 配置、PostgreSQL、Redis 与 Milvus 服务沿用现有配置；仅重建前后端两个应用容器。

## 数据库迁移与备份

升级前已完成 PostgreSQL 自定义格式备份，并通过 `pg_restore --list` 验证备份可读取：

- 数据库：`ruoyi-fastapi`，数据库容器：`litellm-db`。
- 备份：发布目录的 `private/ruoyi-before-chat-work-20260915.dump`。
- 备份 SHA-256：`73a5627aa84f60c1c6ef0d988ef3e10dce69108e22215848578ecf6156cbb279`。

智能体扩展通过插件 CLI 从 `0.3.0` 升级到 `0.4.0`。PostgreSQL `004_work_tasks.sql` 执行成功，共 7 条语句，迁移校验值为 `34e9a8bad29d8140c1e5d2741d07f699812cba897be0241ac20bcaa168ff2f42`。已安装的 001–003 迁移校验一致，跳过重复执行。

构建模式不允许热修改插件，升级使用一次性维护容器，将同一 PostgreSQL 配置作为该容器的 dev 配置供插件 CLI 使用。在线后端始终运行 `dockerpg`；没有改变生产服务的运行模式或全局传输策略。维护入口保存在发布目录的 `maintenance.sh`。

## 实际验收

以下验收直接使用 `12580` 的 Nginx 代理和现有数据库，没有拦截或模拟业务接口：

- 首页和 `/docker-api/openapi.json` 返回 200，注册 11 个工作任务操作；未登录调用工作列表返回业务码 401。
- admin 的真实 `getInfo` 权限通过；创建临时应用与两个已发布智能体，使用现有 `deepseek-v4-flash` 模型。
- 智能体 A 产生随机标记；智能体 B 的提示词和用户消息均不含该标记，B 成功从共享任务历史复述标记。
- 创建请求幂等、运行请求重放、SSE 事件来源及发布版本通过；重放复用原运行和消息。
- PostgreSQL 保留 4 条消息和 2 份成员配置快照；运行结束释放 `currentRunId`。完成、重新打开、归档均通过。
- 真实浏览器桌面 `1440×960` 和手机 `390×844` 均通过：空态中心切换、聊天技能入口、工作目标、已发布智能体列表、消息来源与版本、归档只读、刷新恢复。没有页面/控制台错误、接口错误或横向溢出；浏览器验收没有发送业务写请求。

初次联调脚本遗漏发布智能体必需的应用绑定，已补齐临时应用；页面脚本的悬浮提示交互和行内定位也已修正。这些是验收脚本调整，没有更改已部署产品代码。

验收后已删除临时工作任务、两个临时智能体，停用并软删除无 API key 的临时应用，撤销独立登录会话；首次失败尝试留下的草稿智能体和会话也已清理。主机私有状态副本中的 token 已移除。数据库原始数据与升级前备份保留。

证据位于发布目录的 `live-summary.json`、`browser-summary.json`、`live-cleanup.json` 和 `screenshots/`；可重复执行的脚本及说明位于 `probes/`。私有配置、备份和状态均不提交 Git。

## 运行与回退定位

当前组合使用 `.worktrees/antd-minimal-loop/docker-compose.pg.yml` 与本发布目录的 `compose.override.yml`，项目名为 `ruoyi-vue3-fastapi`，环境文件为本发布目录的 `private/compose.env`。日常操作应继续使用这一组合，避免旧 override 把前端挂载切回旧产物。

旧后端镜像 `ruoyi-backend-pg:milvus-integration-20260909`、旧静态产物 `data/agent-releases/20260909-knowledge-integration/frontend-final/` 和升级前备份均保留。需要回退时应明确切换应用镜像及静态挂载；不得直接恢复旧数据库覆盖部署后的用户数据。

`12584` 仍为隔离工作树的开发预览，原开发代理 `9099` 未启动，不作为当前使用入口；临时构建预览 `12585` 已停止。
