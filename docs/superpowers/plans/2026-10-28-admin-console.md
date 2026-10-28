# 后台管理恢复计划与接入契约

用户已要求补回独立“后台管理”入口及对应功能。延续 AI 内置、identity/operations 各自负责的架构，沿用当前中文控制台 UI。当前工作区继续服务；隔离目录实现并验证后同步回用户现有 VS Code 根目录。不恢复旧插件生命周期、代码生成或旧数据库依赖。

## 范围与任务

- [ ] identity：用户增改/停用/删除与密码重置；角色及实际接口权限；部门树、岗位、菜单元数据；在线会话查看及强制退出。保护本人、最后一个管理员、历史归属与并发版本。
- [ ] operations：字典类型/数据、系统参数、公告 CRUD，登录与操作日志分页及筛选；真实系统/数据库/Redis/Worker 状态。参数与菜单仅影响有明确接入的行为，不提供任意代码、SQL或Redis清库入口。
- [ ] frontend：admin 独立入口、二级导航和上述真实页面；普通账号按权限显示入口，直接调用无权限接口仍403；写操作防重复、版本冲突提示、删除确认、密码不回显。
- [ ] root：公共授权接口、认证 DTO 与权限接线；日志接线、显式迁移0010、schema inventory、隔离PG/Redis/API/Worker和真实浏览器验证；迁移前备份、原数据指纹和正常源码实例升级。
- [ ] 独立规格与质量审查，修复实际问题；前后端规范/测试、升级与已有AI回归后交付。完整保护旧55份跟踪及2份未跟踪优化。

## 共用协议（各实现者遵循，变动先协调）

- 新增 Principal.permissions: frozenset[str] = frozenset()，require_permission(code) 允许 is_admin 或明确权限，旧三参数构造兼容。权限从启用角色实时计算，不靠前端保护。用户 me/login 响应增加 permissions: string[]。
- 权限代码 system:<area>:read/write；area 为 users, roles, departments, posts, menus, dictionaries, configs, notices, logs, sessions, monitor。logs/monitor只有read。超级管理员保留既有is_admin。后台入口允许任一system权限。
- API统一 /api/admin，GET集合返回 {items:[], total:int, page:int, page_size:int}；GET详情返回实体。写入POST创建返回201实体，PATCH更新返回实体，DELETE成功204；PATCH/DELETE必须expected_version，DELETE用query。列表使用search,page,page_size；可增加status/category等明确筛选。所有实体id UUID，version乐观锁，created_at/updated_at可选但不得伪造。
- /users：username, display_name, email, phone, department_id|null, post_ids:UUID[], role_ids:UUID[], is_active, is_admin, remark；POST加password，PATCH不接password（/users/{id}/password独立POST含password/expected_version）。username不随编辑更改；密码永不出DTO。超级管理员标志、角色授权均防越权；删除采用软删除保留业务引用。
- /roles：code,name,permissions:string[],is_active,remark；实际权限目录 GET /permissions => {items:[{code,label,group}]}。code创建后稳定。
- /departments：parent_id|null,name,sort_order,leader,phone,email,is_active；GET仍为扁平items供树组件构造，防环、子部门和用户占用保护。
- /posts：code,name,sort_order,is_active,remark；有关联时不能删除。
- /menus：parent_id|null,name,path,permission,sort_order,is_visible；只允许已接通内部页面，菜单配置可控制后台导航名称、顺序及可见性，不能绕过后端固定权限。权限目录真实控制API。
- /sessions：GET返回session_id, user_id,username,created_at,expires_at,is_current；DELETE /sessions/{session_id}强制退出，不能返回登录令牌或其摘要。current会话不可误踢。
- /dictionaries：code,name,is_active,remark；/dictionaries/{id}/items：label,value,sort_order,is_active,remark；字典类型有数据时不能误删除。
- /configs：key,name,value,remark,is_active；仅平台可公开维护的业务参数，不管理部署env或供应商密钥，不返回秘密。首个接入参数 console.notice_enabled 控制公告可见性，true/false服务端校验。
- /notices：title,content,kind（notice/announcement）,status（draft/published）,remark；发布内容登录用户通过 GET /notices/published 获取，参数禁用时返回空集合；前端通知面板需实际接入。
- /logs：只读GET，kind=login|operation，outcome=success|failed，search,page,page_size，返回id,kind,action,username,resource_type,resource_id,outcome,message,created_at。不返回密码、密钥、Cookie、完整请求体或敏感URL；已接通管理写入在同事务记录操作日志，登录成功/失败记录登录日志。
- operations.record(session, *, principal:Principal|None, action:str, resource_type:str='',resource_id:str|None=None,outcome='success',message='',kind='operation',username='') -> None，为认证及identity路由调用的公共日志接口。
- /monitor：根任务提供真实API，前端以返回的platform/database/redis/worker四块展示，健康状态与真实采集值；不虚构CPU/性能图。另 /overview 返回有权限可见模块及数量，具体由根任务整合，不作为前端必须依赖。

## 分工边界

identity实现者拥有 modules/identity 新增admin*文件、orm.py/service.py/public.py扩展，api/routes/admin_identity.py，自己的测试。不要改Principal/auth/container/main/migration。
operations实现者拥有 modules/operations、api/routes/admin_operations.py与自己的测试。不要改container/main/auth/migration/identity。
frontend实现者拥有 modules/admin 和自己的browser测试；不要改 app、identity API、topbar/accountmenu（根任务接入口与公告）。
根任务拥有shared.principal、api/auth/main、bootstrap、迁移、运行验证及最终同步。新DDL由根任务统一。
