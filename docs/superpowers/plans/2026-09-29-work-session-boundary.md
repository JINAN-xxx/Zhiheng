# Work 状态归属与会话清理实施计划

**目标：** 按用户同意的下一轮范围，将 Work 状态归回扩展模块、平台通过公共入口协作，并完善 release 分支自动检查。

**架构：** Work 的 Pinia store 移至插件内，原入口保留兼容转发。公共导航接口只暴露侧栏需要的列表状态和操作。平台提供按 Pinia 实例隔离的会话生命周期插件，各 store 通过 `sessionCleanup` 声明自己的同步清理动作；用户会话模块统一触发，清理时不创建未加载的业务 store。

**技术栈：** Vue 3、Pinia 3、Node ESM、Vite、pytest、GitHub Actions。

## 设计决定

- 完整保留 Work 的任务执行/轮询/重试逻辑及递增的请求代次。清理后旧列表、详情、创建、目录和流响应不得覆盖新会话。
- 新公共文件 `plugins/agent_extension/public/workTasks.js` 提供 `useWorkTaskNavigation`、`useWorkTaskStore` 和权限能力查询。宿主侧栏使用导航接口，插件页面使用自己模块的 store。
- `src/store/sessionLifecycle.js` 通过 Pinia plugin 记录已经创建的 store、所属 Pinia 和清理动作；处置 store 时注销。一次清理仅影响同一 Pinia 实例，单个清理动作失败不能阻止其他模块或令牌清理。
- 用户模块移除对扩展插件的直接引用。退出、401 原本都调用 `clearSession`，继续复用；成功登录前也调用清理，覆盖直接切换账号。失败登录不清理当前有效会话。
- Work 清理同步重置列表/详情加载标记、错误、分页、草稿、任务与智能体列表，停止流和轮询，保留请求代次失效机制。
- 不采用只搬文件的方案，因为这不能消除登录模块的业务依赖；不引入通用事件总线，因为当前需求只需要明确的会话清理生命周期。
- CI 是独立文件范围，由并行工作者处理：master 与 release/** 的 push/PR 覆盖，显式测试依赖及 Lua 支持。前端新边界测试由已有 test:plugin 自动执行。

## 1. 测试先行

- [x] 记录前态代码与线上 dist 哈希，运行现有前端测试。
- [x] 新增真实 Pinia 会话生命周期测试：实例隔离、未加载 store、dispose、故障隔离、多模块清理。
- [x] 扩展 Work 回归：清理加载/错误状态、丢弃迟到列表与详情、停止流与轮询、连续清理和新会话可用。
- [x] 验证公开入口/兼容入口保持同一 store，侧栏动作触发实际 API，响应与错误继续透传。
- [x] 测试用户真实 actions：退出、401 使用的 clearSession、成功/失败登录对旧 Work 状态的影响。
- [x] 独立审查发现的既有迟到 401 竞态：增加真实 Axios/Pinia 回归，按发送请求的 Authorization 与当前令牌匹配后才允许清理；验证旧身份/匿名请求拒绝但不清新会话，当前身份 401 仍正常清理和跳转。
- [x] 新测试先在缺少公共入口或未重置状态时失败。

## 2. 前端实施

- [x] 新建 `src/store/sessionLifecycle.js`，在 `src/store/index.js` 安装生命周期插件。
- [x] 移动 `src/store/modules/workTask.js` 实现至 `plugins/agent_extension/stores/workTask.js`，声明清理动作并补全 reset；原文件仅兼容转发。
- [x] 新建 `plugins/agent_extension/public/workTasks.js`；侧栏与 Work 页面接入相应公开能力。
- [x] `src/store/modules/user.js` 使用公共会话清理；删除仅用于旧全局回调的 `plugins/agent_extension/utils/workTaskSession.js`。
- [x] 保留原 Work 行为断言，测试执行生产模块，新增宿主不导入扩展内部实现的边界检查。

## 3. 验证、审查和记录

- [x] 前端完整 `npm run test:plugin` 通过。
- [x] 在临时副本构建；浏览器回归使用临时开发服务；确认运行中的 dist 哈希未变。
- [x] 独立审查生命周期、状态隔离、兼容入口、CI 修改与测试证据。
- [x] YAML、分支匹配与测试依赖验证通过；明确本地验证不等于云端全矩阵运行。
- [x] 将已验证的 Work 桌面/移动浏览器检查接入现有 Playwright 临时服务及截图上传步骤。
- [x] 更新架构和实施记录；`git diff --check` 通过，保留本地改动。

前态源码和构建哈希：`/tmp/zhiheng-work-boundary-kjuok0i6`。工作区仍为 `/home/ja/project/zhiheng`、`fix/platform-integration-review`；已有改动保留，不更新运行服务。
