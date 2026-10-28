# 知识库文件入口整合

用户已确认：知识资料在知识库完成管理，对话附件留在对话；共享文件模块承担存储、权限、引用和回收，不作为独立一级业务入口。沿用现有界面和源码运行模式。

## 设计与实施

- [x] 主导航删除“文件管理”。管理员在知识库首页进入“存储管理”，普通成员不展示此入口。
- [x] 存储治理页面的正式地址改为 `/knowledge/storage`，归属“知识库”导航；保留 `/files`、`/system/file` 作为旧链接别名，继续执行管理员路由校验。
- [x] 复用 `modules/files` 的统计、筛选、详情、ACL、引用、回收、审计、保留策略和对账，页面注明同时管理知识文档和对话附件。提供返回知识库按钮。移除治理页的独立上传入口，知识文档从具体知识库上传并建立索引，对话附件从对话上传。
- [x] `KnowledgeOverview.vue` 通过可选能力参数和事件展示入口，父页面根据登录身份接线。知识文档的文件夹、上传、预览、权限、删除和索引任务沿用现有实现，不迁移字节、不修改数据库或引用。
- [x] 浏览器验证管理员从知识库往返、旧地址兼容、普通成员入口与路由拒绝、移动端布局，以及原知识文档和治理能力。HTTP 夹具只拦截源站 `/api/`，不拦截 Vite 模块源码。
- [x] 执行前端 core、类型、规范检查与针对性浏览器回归；在正常入口进行真实登录和只读核对。更新 README 和验收记录，保护旧项目未提交文件。无需重新打包或手动重启服务。
- [x] 修复正常实例实测发现的对账统计 500；真实分组结果回归与 API/抽屉复测通过，保留管理员权限边界。

## 涉及文件

- `frontend/src/app/components/PlatformSidebar.vue`：一级业务导航。
- `frontend/src/modules/knowledge/components/KnowledgeOverview.vue`、`views/KnowledgeView.vue`：管理员存储入口与页面事件。
- `frontend/src/modules/files/routes.ts`、`views/FilesView.vue`：正式路由、旧链接、管理员权限和治理页面。
- `frontend/tests/browser/{console_regressions,files_regressions,developer_regressions}.py`：导航、路由和源码运行回归。
- `README.md`、本计划和验收记录：当前入口与验证证据。
- `backend/src/zhiheng/modules/files/service.py`、`backend/tests/unit/test_file_issue_stats.py`：对账分组统计修复与回归。

结果见[验收记录](../verification/2026-10-28-knowledge-storage.md)。
