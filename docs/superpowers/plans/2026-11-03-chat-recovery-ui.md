# 普通对话失败恢复与消息界面修复

**需求：** 按用户截图精简回复状态外观，补齐首次调用失败后的重新生成，移除普通对话的知识库挂载入口；仅验收桌面，不打包前端。

**已核根因：** 首次运行只有 user 消息，成功后才写 assistant；失败悬浮回复只渲染默认复制、朗读操作，现有重新生成服务仅接受 assistant。对应实际运行日志为 TimeoutError，run 失败、正文 0 字、思考 219 字。

**实现边界：** 沿用原重新生成端点、请求幂等、会话锁、模型/附件/技能快照和用量账本。允许以最新问题作为首次失败的重新生成目标，但必须是本人当前会话、原 chat Run 已终止且该问题尚无 assistant 回复。已有回复继续以 assistant ID 重新生成。普通聊天不再新增知识库选择，历史消息引用及旧请求恢复继续兼容。

- [x] 保存原有未提交文件指纹。
- [x] 后端回归：首次失败/取消/中断可重试，原问题只保留一次；旧问题、正在运行、已回答、他人会话、归档会话拒绝；同请求重放不重复计量。
- [x] 前端回归：失败无正文能重新生成且保留下一条草稿、未知接纳沿用原请求重试、刷新后可恢复；普通聊天无知识库选择，发送不挂载知识库。
- [x] 修复 ChatService 目标校验、ChatView/工作区的失败回复操作；状态使用无边框文字，空回复仅显示有意义的重新生成操作。
- [x] 在独立 PostgreSQL/Redis 与本地协议 Provider 执行后端回归，执行原消息操作/会话恢复的桌面回归及前端 core、typecheck、lint/format。
- [x] 先后进行规格与质量复查；核无活动模型任务后重载当前 API/Worker，检查就绪、原数据/修改保护，精确清理本轮私有测试服务。

涉及文件：`backend/src/zhiheng/modules/chat/service.py`、`backend/tests/core/integration/test_chat_regeneration.py`、`frontend/src/modules/chat/{views/ChatView.vue,composables/workspace.ts}`、`frontend/src/shared/components/MessageActions.vue` 以及相关消息/知识库选择浏览器回归。

## 验收结果

2026-11-03：后端 43/43（无 skip），覆盖首次失败/取消/中断重试、并发、旧轮次/归属/归档、原请求重放及计量。前端消息操作 18 个独立用例与旧聊天准备恢复 10 项均闭合；原完整消息操作首轮 17 PASS、1 个页面初始化超时，原用例不改源码复验通过，与兼容回归组合 11/11；没有声称 28 项曾一次完整全绿。core 111/111、typecheck、ESLint、Prettier、Ruff、mypy 220 个源文件及架构检查通过。规格和质量独立审查 PASS。

正常实例在 Run、检索准备及知识作业均无活动任务时重载 API/Worker，镜像及 schema16 保持，就绪检查通过；14 张模型/计费/聊天/Run 表前后指纹一致。正常实例只读事务核验较早失败被最新轮限制拒绝、当前最新无回复终态问题可重新生成，没有创建运行或计费。测试仅使用隔离 PostgreSQL/Redis 和本地协议 Provider；专用容器和网络已精确清理，无前端 build。原 733 项未提交文件按内容、属主与权限核对保留。详细私有证据：仓库外 `chat-recovery/20261103/verification.json`、两份审查、`reload-proof.json` 与 QA 运行/清理记录。
