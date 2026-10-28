# 智衡控制台 UI 接入计划

> 使用 writing-plans、dispatching-parallel-agents 执行；按互不重叠的业务目录分工，完成后由主任务统一检查规格、代码和回归结果。

**目标：** 将用户已选定的 `frontend/design-previews/bailian` 视觉设计接入正式 Vue 前端，保留全部真实业务能力。

**架构：** 正式路由、业务组件、API、权限、流式运行与账本继续承担业务；预览只作为视觉和交互依据。统一主题和公共组件负责字体、色彩、布局和表格；各模块保留自己的状态与业务边界。不把静态预览状态、示例密钥或模拟回复迁入正式代码。

**技术栈：** Vue 3、TypeScript、Ant Design Vue、现有消息渲染及知识图谱组件；不新增运行依赖。

## 保护与验证基线

- [x] 在当前 VS Code 工作目录创建 `feat/bailian-console-ui` 分支。
- [x] 备份正式前端源码、测试和配置；记录后端源码及原有 55 份未提交文件哈希，保留原有未跟踪文件。
- [x] 记录现有 core、typecheck、lint、format 基线，修复只限本次改动引入的问题。

## 1. 主题、框架和工作台（主任务）

文件：`frontend/src/app/App.vue`、`app/components/PlatformSidebar.vue`、`PlatformTopbar.vue`、`PlatformAccountMenu.vue`、`app/router.ts`、`shared/theme.ts`、`shared/styles/*`、`shared/components/*`；新增独立工作台/搜索组件。

- [x] 使用系统字体栈；14px 常规正文、400/500/600 字重、16px 分区标题、白色导航、浅灰页面与蓝紫色操作。
- [x] 导航按工作台/对话/模型/应用构建/知识与数据/开发与用量组织；品牌只保留“智衡”，保留真实登录、退出、主题、管理员限制与手机导航。
- [x] 工作台数量及资源搜索来自实际有权读取的接口；请求失败显示不可用及重试，不以示例或零值替代。
- [x] 公共页头、筛选、表格、卡片、抽屉和空态统一；保留键盘、焦点、暗色、触屏与原始操作入口。

## 2. 智能体和技能（独立目录任务）

文件：`frontend/src/modules/agents/{views,components,routes.ts}`、`frontend/src/modules/skills/{views,components}`。

- [x] 对照预览完成列表与编辑器视觉；智能体十项配置、草稿/发布/试聊/停止/删除/API 文档全部保留。
- [x] 技能包继续由正式解析/确认/安装流程处理 SKILL.md/ZIP；保留内容、版本、启停、引用保护和删除限制。
- [x] 不修改业务 API、presentation、intent、授权与后端。

## 3. 模型、开发接入和用量（独立目录任务）

文件：`frontend/src/modules/models/{views,components}`、`developer/{views,components}`、`usage/views`。

- [x] 对齐模型/连接/费率、应用详情/密钥/文档、额度和用量的视觉与信息层次。
- [x] 保留原有全部字段、验证、启停发布、引用保护、密钥一次展示/轮换/撤销、真实调用与未知核算状态。
- [x] 保持现有组件语义与可访问名称，便于既有功能回归；不改账本和模型调用接口。

## 4. 知识库和文件（独立目录任务）

文件：`frontend/src/modules/knowledge/{views,components}`、`files/{views,components}`。

- [x] 列表/文档/检索/图谱、文件治理各子页与通用控制台样式一致。
- [x] 保留上传恢复、任务/取消/历史、图谱配置与画布、原文权限交集，以及文件 ACL/引用/回收/审计/保留/提醒/对账。
- [x] 使用现有真实数据与任务，不添加模拟解析、OCR 接通或云端 MinerU 行为。

## 5. 对话和工作（主任务）

文件：`frontend/src/modules/chat/{views,components}`、`frontend/src/modules/work/{views,components}`，新增小型消息操作组件。

- [x] 将历史会话/工作列表放在各自内容区，主导航保持完整；保留搜索、新建、切换、归档、重命名、删除及引用处理。
- [x] 对话用户靠右、模型靠左；历史栏紧凑，删除菜单使用红字。
- [x] 消息下方使用参考图的灰色图标操作栏；复制提问和回复原文可用，其余没有正式业务能力的图标明确禁用。
- [x] 保留附件、技能、知识来源、Markdown/代码/公式/Mermaid、停止、恢复、原请求重试、工作冻结成员与版本。
- [x] 滚动由原生容器负责；不引入全局阻塞滚轮监听。

## 6. 统一验收与启用（主任务）

- [x] `npm run test:core`：原有 intent/stream/transport 测试通过。
- [x] `npm run typecheck`、`npm run lint:core`、`npm run format:check`：零错误。
- [x] `npm run build:platform`：正式构建通过。
- [x] 在独立前端实例用现有 Chromium HTTP/SSE 边界夹具回归全部业务页面；只对确有变更的导航/布局契约调整选择器，不删断言、不改真实功能预期。
- [x] 追加工作台、完整导航、消息左右/复制、技能包、管理员/普通用户、暗色和手机布局检查，确认无真实供应商调用。
- [x] 对照已选预览逐页审查视觉，核对原功能清单与修改范围，关闭发现的问题。
- [x] 启用正式前端前保留现用构建/镜像与恢复方式；只更新前端服务，后端、数据库和业务数据保持当前状态。
- [x] 在正常入口检查登录、真实只读资源页面与静态资源；记录启用结果及遗留限制。

实现和验证记录保存在私有备份目录 `ui-integration/20261028`，避免把本机凭据、测试数据或部署私密信息写入仓库。

最终结果见[控制台 UI 验收](../verification/2026-10-28-console-ui.md)。浏览器统计按唯一 test.id() 去重：182 个唯一用例，194 次矩阵执行；原始等待超时和定向复测范围在验收中单列。
