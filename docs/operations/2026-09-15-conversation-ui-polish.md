# 智能对话 UI 美化发布

2026-09-15 已按用户要求更新 `12580` 前端，保留智能对话内的聊天 / 工作切换。

## 视觉调整

- 空白聊天的输入卡片移到欢迎区下方；已有聊天继续使用底部输入。模式切换、欢迎图标、快捷提示与输入卡片统一间距、圆角和主题色。
- 工作目标、成员及本轮执行者组成一张卡片；智能体选择弹窗区分名称、说明、版本及选中状态。
- 工作消息、成员标签和底部输入统一阅读宽度与视觉层次；技能弹窗适配窄屏，打开时不再叠加触发按钮提示。
- 对话页去掉旧的手机底部导航预留，保留设备安全区和可选版权页脚。多成员列表独立滚动，短屏任务允许纵向滚动。

## 验证

`npm run test:plugin` 共 31 组通过，Docker 模式生产构建成功。现有 `chat_work_mode_check.py`、`work_access_check.py`、`work_task_actions_check.py` 均直接针对最终构建产物通过，覆盖聊天/技能、双智能体接续、权限、恢复、取消及任务生命周期。

小屏回归增加 320px、10 个长名称成员、7 行输入场景，并用 `to_be_in_viewport(ratio=1)` 检查祖先裁切后的完整可见性。该断言先复现底部遮挡，修复后通过。状态标签保留错误语义颜色，目标卡片具备焦点反馈；独立复审无剩余 Critical / Important 问题。

发布后检查 `12580` 首页与新产物完全一致，真实 OpenAPI 返回 200 且保留 11 个工作任务操作；后端 `19099` 持续健康。桌面 1440px、手机 390px/320px 和深色模式的最终截图位于本发布目录的 `screenshots/`。浏览器验收使用隔离接口数据，没有创建真实业务实体。

## 部署定位

静态产物：`data/agent-releases/20260915-conversation-ui-polish/frontend/`。仅重建 `zhiheng-frontend`，后端与数据库沿用先前版本。

当前 Compose 组合为 `.worktrees/antd-minimal-loop/docker-compose.pg.yml`、`data/agent-releases/20260914-chat-work-dual-mode/compose.override.yml`、`data/agent-releases/20260915-conversation-ui-polish/compose.override.yml`。项目名为 `zhiheng-vue3-fastapi`，环境文件仍为 `data/agent-releases/20260914-chat-work-dual-mode/private/compose.env`。

旧 `20260915-conversation-navigation` 静态产物和 override 均保留；需要回退时将最后一份前端 override 替换为该版本并重建前端，不涉及数据库恢复。临时 `12585` 预览已停止。
