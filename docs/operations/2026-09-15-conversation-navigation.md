# 智能对话导航修正发布

同日后续已发布 [UI 美化版本](2026-09-15-conversation-ui-polish.md)，当前前端静态挂载以该记录为准；本次单入口和子页结构继续保留。

2026-09-15 已更新 `12580` 前端。聊天和工作都属于“智能对话”：侧栏仅保留一个智能对话入口和一个“新建对话”按钮，模式在页面中心切换，历史列表随模式变化。工作页高亮智能对话，手机顶栏也显示智能对话。

聊天地址为 `/ai/chat`，工作子页地址为 `/ai/chat/work`。旧 `/ai/work` 链接通过前端路由重定向到工作子页，保留任务 ID、其他查询参数和锚点。“新建对话”回到当前模式空态。工作子页继续独立校验权限，不继承聊天权限。

## 验证

- 修改前的回归按预期失败；修改后 `npm run test:plugin` 共 31 组通过。
- Docker 模式生产构建成功，产物为 `/tmp/ruoyi-conversation-nav-build`。
- `chat_work_mode_check.py`：桌面单一入口、中心切换、新建、工作历史恢复、手机抽屉和旧链接兼容通过。
- `work_access_check.py`：5 个只读权限、无工作权限和 320px 场景通过。
- `work_task_actions_check.py`：失败重试、运行恢复及生命周期 3 个场景通过。
- 上述浏览器检查使用真实组件和隔离接口数据；发布后又对 `12580` 实际静态资源运行聊天/工作桌面与手机主流程，通过。
- `12580` 首页内容与发布目录完全一致；真实 `/docker-api/openapi.json` 返回 200，保留 11 个工作任务操作。后端 `19099` 容器持续健康，未重启或修改数据库。
- 独立代码审查没有发现 Critical 或 Important 问题。工作专属落地、旧链接参数及权限隔离另经内存路由验证。

本次没有创建真实业务测试数据。前次真实双智能体调用、持久化和清理证据仍见 [初次启动记录](2026-09-15-chat-work-release.md)。

## 部署定位

新静态产物为 `data/agent-releases/20260915-conversation-navigation/frontend/`，前端容器仍为 `ruoyi-frontend`。部署只重建该容器，继续使用原 Nginx 镜像和配置。

Compose 在初次聊天/工作部署组合后追加以下文件：

```text
data/agent-releases/20260915-conversation-navigation/compose.override.yml
```

完整组合为 `.worktrees/antd-minimal-loop/docker-compose.pg.yml`、`data/agent-releases/20260914-chat-work-dual-mode/compose.override.yml`、上述新 override；项目名仍为 `ruoyi-vue3-fastapi`，环境文件仍为 `data/agent-releases/20260914-chat-work-dual-mode/private/compose.env`。

前次 `20260914-chat-work-dual-mode/frontend/` 产物保留，可通过移除最后一份前端 override 并重建 `ruoyi-frontend` 回退；不涉及数据库恢复。
