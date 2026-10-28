# 工作空间与普通聊天分开

用户已明确“工作空间”与普通聊天分开。工作空间保留现有任务、成员接力、历史和运行恢复，通过主导航独立进入；沿用当前源码运行模式。

- [x] 移除 `WorkEmptyState.vue` 的聊天/工作切换、`canChat` 参数与 `mode` 事件，清理 `WorkView.vue` 的聊天跳转和无其他调用者的共享切换组件。
- [x] 聊天状态初始化仅在 `route.name === 'chat'` 时加载，直接打开工作空间不读取普通聊天历史。保留现有状态提供器与切页清理行为，以及工作请求的重复提交保护。
- [x] 工作路由标题与导航统一为“工作空间”；旧地址 `/ai/chat/work` 继续指向工作空间并选中对应导航。
- [x] 更新页面回归：两页都不展示模式切换，通过主导航往返；直接打开新旧工作地址不访问普通聊天接口；工作任务创建、成员接力与历史回归继续通过。
- [x] 验证前端核心、类型、规范与相关浏览器检查，在正常实例做只读核对；更新 README，保护原有未提交优化。本轮不构建安装包，不创建真实工作或调用收费模型。

主要文件为 `frontend/src/modules/work/components/WorkEmptyState.vue`、`views/WorkView.vue`、`routes.ts`、`frontend/src/modules/chat/composables/workspace.ts`、`frontend/tests/browser/work_regressions.py` 和 `README.md`。

后台管理是另一个已核实的迁移缺口，目前尚未接回；本次工作空间调整不将其标记为完成。

结果见[验收记录](../verification/2026-10-28-work-isolation.md)。
