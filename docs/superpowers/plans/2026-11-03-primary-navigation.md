# 主导航分组与折叠实现计划

**目标：** 按已确认的“工作台独立，应用、资源、开发与用量三组”整理桌面主导航，记住每位用户的分组折叠偏好。

**架构：** 仅在 `frontend/src/app/components/PlatformSidebar.vue` 内维护固定导航配置和展示偏好。已有路由、权限守卫与左下角 `PlatformAccountMenu` 继续复用。分组状态按用户 ID 写入浏览器本地存储，仅保存组 ID；读取、解析或写入失败时导航仍可使用。

**技术栈：** Vue 3、Vue Router、TypeScript、现有 Ant Design 图标、Playwright；使用当前 19080 源码实例验收，不打包，不改后端。

## 已确认结构和交互

```text
工作台
应用：对话体验、智能体、工作空间
资源：模型目录、知识库、技能管理
开发与用量：开发接入、用量与调用
左下角账号菜单：个人资料、按权限进入后台管理、退出登录
```

分组标题为原生按钮，支持键盘，提供 `aria-expanded`、唯一的 `aria-controls`。默认全部展开；当前路由所属分组保持展开，其自动展开不覆盖用户原先的偏好。侧栏缩成图标时全部入口可用，恢复完整侧栏后继续使用分组偏好。沿用路径前缀和 `route.meta.activeMenu` 的选中规则。

## 任务一：记录现状并添加行为回归

**文件：** 新建 `frontend/tests/browser/navigation_regressions.py`，复用 `DeveloperBoundary` 的隔离 HTTP 夹具。

- [x] 保存原有未提交文件指纹，确保本次任务不覆盖此前 API 目录和 legacy 改动。
- [x] 添加桌面浏览器测试：折叠只隐藏本组、URL 不变、刷新记忆；当前组自动展开、离开后恢复记忆；图标侧栏全部入口可用。
- [x] 添加不同用户偏好隔离、本地存储不可用的回归；复用现有后台入口和个人资料回归。
- [x] 运行 `PLATFORM_BROWSER_URL=http://127.0.0.1:19080 python3 -m unittest navigation_regressions -v`，确认失败来自缺少分组按钮。

## 任务二：实现并验证

**文件：** 修改 `frontend/src/app/components/PlatformSidebar.vue`。

- [x] 将 `groups` 改为具有稳定 ID 的三组，把工作台作为独立入口。
- [x] 用原生按钮和 `v-show` 面板实现折叠；路由选中继续使用原 `active(path)`，当前组强制展开。
- [x] 用 `identity.value.id` 分隔本地存储键，校验存储数组中的合法组 ID；存储异常仅回退默认展示。
- [x] 图标模式显示全部入口，使用 Vue `useId()` 避免重复控件 ID；保持白底、现有字体、间距和账号入口。
- [x] 运行新增桌面浏览器回归、既有桌面工作台/账号/后台入口及 API 说明回归，确认无浏览器错误。
- [x] 运行 `npm run test:core`、`npm run typecheck`、变更文件 ESLint 和 Prettier；检查桌面截图、就绪接口与原文件指纹，记录验证结果。

本计划在当前会话内按步骤执行，保留当前源码运行入口和既有未提交内容。

## 验证结果

2026-11-03：新增桌面导航回归 3/3，通过既有工作台、异步路由、个人资料、后台入口权限和 API 目录的 9 项桌面回归；合计 12 项。前端 core 111/111，typecheck、变更组件 ESLint、Prettier 与差量检查通过。1440×900 下展开、折叠分组和图标侧栏截图已检查，浏览器无页面错误。首次行为回归在实现前因缺少“资源”分组按钮失败，原断言在实现后通过。此前 730 项未提交文件逐项核对内容及属主、权限；私有证据见仓库外 `primary-navigation/20261103/verification.json`。源码实例直接生效，无前端打包或后端改动。
