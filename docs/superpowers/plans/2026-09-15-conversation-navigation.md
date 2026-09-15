# 智能对话导航修正计划

> 在当前会话按 executing-plans 执行，用户已明确纠正所需层级，继续完成实现和部署。

**目标：** 把工作收回智能对话内部，侧栏仅保留一个入口和一个新建按钮。

**架构：** 保留现有聊天/工作组件和独立状态，将工作规范路径改为 `/ai/chat/work`；旧路径重定向。主导航按对话所属页面高亮，子页面权限继续显式校验。

**技术栈：** Vue 3、Vue Router、Pinia、Ant Design Vue、Node assert、Playwright、Vite、Nginx。

## 1. 导航与权限回归

- [x] 修改 `tests/plugins/workspaceTransforms.test.js`，断言工作子页被识别为 work、工作专属权限才能授权该路径、仅工作权限可以作为落地页。
- [x] 修改 `tests/plugins/platformShellContract.test.js`，要求一个新建对话入口且没有独立工作菜单。
- [x] 运行 `node tests/plugins/workspaceTransforms.test.js` 和 `node tests/plugins/platformShellContract.test.js`，确认现实现因上述行为不符失败。
- [x] 在 `src/router/index.js` 注册 `/ai/chat/work`，保留 `/ai/work` 到新路径的重定向与 query/hash；在 `workspaceTransforms.js` 保持工作子页独立授权。
- [x] 修改 `PlatformSidebar.vue`、`PlatformTopbar.vue`、`layout/index.vue`，统一导航和标题；聊天、工作组件内跳转改用子路径。

## 2. 页面验收

- [x] 更新 `tests/e2e/chat_work_mode_check.py` 的中心切换、新建、菜单选中及历史恢复场景；更新 `work_access_check.py` 和 `work_task_actions_check.py` 的规范路径，保留旧链接兼容检查。
- [x] 运行 `npm run test:plugin`，全部通过后构建 Docker 模式静态产物。
- [x] 用现有三个 Playwright 脚本验证构建产物，检查桌面/手机导航、权限和工作生命周期。

## 3. 发布

- [x] 仅提交相关源码、测试和文档，保留原工作区改动。
- [x] 将构建产物放入独立发布目录，更新当前前端挂载并仅重建 Nginx 容器。
- [x] 验证 `12580` 返回新产物、接口正常；保留旧产物可回退并记录版本。
