# 个人资源与开放 API 实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 执行；每个实现任务提供完整上下文，依次完成规格审查和质量审查。用户已授权实施，不重复征求工作区或执行方式许可。

**目标：** 使技能、知识库按个人隔离，并交付平台文件/知识库/技能/操作资源 API、Key资源授权与准确文档。

**架构：** 在 `feat/personal-resource-api` 独立工作树实施，Console和资源协议通过应用用例复用领域规则。领域模块保留数据所有权；模型执行、账务、Worker与既有作业保持单一实现。

**技术栈：** 当前 Vue/TypeScript、FastAPI/Pydantic、SQLAlchemy/PostgreSQL、Redis/ARQ、Milvus、LangChain 1.4.7；复用已有开发环境和缓存镜像，不重新打包前端。

## 文件责任与工作顺序

- `modules/skills/{orm,contracts,access,builtin,service,public}.py`、`0014_personal_skill_ownership.py`：个人技能、内置副本、旧归属和版本关联。
- `modules/knowledge/service.py`：本人默认列表、私有读取/管理与主动共享边界。
- `application/identity/accounts.py`、CLI/管理员用户用例：创建用户时同事务初始化个人模板，领域identity不反向依赖技能。
- `modules/developer/{orm,contracts,keys,scopes,resource_grants,api_requests}.py`、`0015_resource_api_access.py`：Scope、应用资源绑定、请求身份与容量/频率限制。
- `application/access/`：服务端CallerContext、当前权限交集与阶段守卫。
- `application/{files,knowledge,skills,operations}/`：命令/查询、跨域事务与原作业门面。
- `api/{common,console,resources,inference}/`：HTTP协议、稳定旧路径、新`/api/v1`和完整Schema；`api/main.py`统一装配。
- `frontend/src/modules/{skills,knowledge,developer}/`：个人操作、资源授权及分组文档，界面风格沿用。
- `tools/check_architecture.py`、`backend/tests/`、`frontend/tests/`：固定依赖方向、隔离与真实链路回归。

## 任务一：个人资源基础

- [x] 保存正常schema/积分/Key/模型与57文件基线，在独立PG/Redis/Milvus运行原技能/知识用例基线。
- [x] 增加 `test_personal_resources.py`：管理员与普通用户分别安装同slug；UUID读取、修改、删除及版本均不得跨人；普通用户可管理本人；管理员普通列表也不默认跨人；私有KB不可被另一管理员通过普通入口管理。

关键HTTP红例使用已有 `admin/app/provider` 夹具，不需要新的假调用器：

```python
async def test_skill_owner_isolation(admin, app):
    import httpx
    package = b'---\nname: own-note\ndescription: Personal notes\n---\nUse supplied facts.'
    prepared = await admin.post('/api/skills/import/prepare?filename=SKILL.md', content=package,
                                headers={'Content-Type': 'application/octet-stream'})
    assert prepared.status_code == 201
    first = await admin.post('/api/skills/import/' + prepared.json()['draft_id'] + '/confirm', json={})
    assert first.status_code == 201
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        assert (await member.post('/api/auth/login', json={'username':'member','password':'platform-test-password'})).status_code == 200
        assert (await member.get('/api/skills/' + first.json()['id'])).status_code == 404
        other = await member.post('/api/skills/import/prepare?filename=SKILL.md', content=package,
                                  headers={'Content-Type':'application/octet-stream'})
        assert other.status_code == 201 and other.json()['action'] == 'install'
        second = await member.post('/api/skills/import/' + other.json()['draft_id'] + '/confirm', json={})
        assert second.status_code == 201 and second.json()['id'] != first.json()['id']
```

- [x] 运行红例确认当前全局查询/权限失败；再实现 owner+slug唯一、个人CRUD、模板副本和所有技能调用上下文。
- [x] 草稿/确认按当前owner定位；版本绑定、引用保护、Agent发布、Work冻结成员和旧确认结果保持稳定，不能用全局slug阻挡另一用户。
- [x] 知识库默认列表仅本人；可用共享由本人ACL明确表达，管理永远检查归属。列表和详情不得依赖管理员默认跨人分支。
- [x] 迁移先审计草稿/审计归属；不确定且有活动引用则升级前停止，不分给默认管理员。内置历史关联保留原快照/哈希；当前用户获得个人副本。
- [x] 在CLI与管理员创建用户的应用用例初始化个人副本，禁止通过GET偷偷写初始化数据；更新真实夹具使用同一创建用例。
- [x] 个人与跨域回归：`pytest tests/core/integration/test_personal_resources.py tests/core/integration/test_skills.py tests/core/integration/test_skill_lifecycle.py tests/core/integration/test_knowledge_roles.py tests/core/integration/test_agents.py tests/core/integration/test_work.py -q`。
- [x] 独立规格/质量审查后提交个人域完整切片，不带未解决隔离问题进入资源开放。

阶段一验收：个人归属及历史引用规格复验 12/12、质量并发/事务验证 7/7、管理员 HTTP 复验 11/11；当前 171 文件类型及架构检查通过。正常库 schema13 备份副本升级至 schema14，81 张原表的 1,653 条旧行按原列保持，旧修订及账务不变。整套资源 API 与最终全量回归继续按后续任务执行。

## 任务二：Key资源能力与接入上下文

- [x] 先以新测试证明旧Key访问资源401/403、files-only Key可无model_ids、Key无Scope/跨应用/伪造owner被拒绝；缺模型授权的计量请求不接纳。
- [x] Key添加不可变Scope和独立resource_rpm_limit；旧Key只迁入原推理Scope，轮换保持相同能力。
- [x] 添加应用资源授权及API请求身份表；资源创建绑定应用，已有个人资源由本人显式授权；同Owner不同App也不共享未授权ID。
- [x] `CallerContext`从Cookie或Key依赖构建，不从请求owner/admin字段构建；Key Scope、本人权限、应用授权和当前ACL取交集。
- [x] UUID Idempotency-Key以应用+操作+键唯一，同内容重放，不同内容409；处理中的请求有明确状态，24小时快照过期也不能变成新执行。
- [x] 限额按应用原始文件1000/1GiB、资源请求120每Key每分钟默认，配置与校验同属developer资源策略，Token计量不混用。
- [x] 运行 `test_resource_key_scopes.py`、`test_resource_requests.py`，并回归原developer/public/billing/轮换用例；依次审查、提交。

阶段二验收：当前冻结代码的最终定向 19/19、独立规格 10+1 项与质量 4 项真实 PostgreSQL 验证通过；177 文件类型与架构检查通过。正常备份副本 schema14→15 保留 82 张原表的 1,665 条旧行及旧 Key 原列，新增四表为空；旧 Key 仅保留原推理能力。

## 任务三：文件资源与应用用例

- [x] 红例以纯Bearer客户端 `POST /api/v1/files` multipart上传；同键只创建一份、跨App404、owner字段422、无Scope403、过大413。
- [x] 文件领域仍拥有字节、ACL、引用、回收/恢复；应用用例负责请求登记、授权、文件登记和应用绑定的事务，不用路由直接取ORM。
- [x] 持锁事务之外传输/写存储；提交前复核当前Key/容量。失败只回收本次未登记对象，旧对象保持。
- [x] 实现列表/详情/Range内容/软删恢复，If-Match映射expected_version；保留Console旧路径并将资源动作接到同一用例。
- [x] 只增HTTP multipart解析必要依赖并精确锁定；不得升级模型SDK或改计费单位。现有缓存镜像需加入新依赖时仅构建后端一次，不构建前端。
- [x] `pytest tests/core/integration/test_resource_files.py tests/core/test_files.py -q`；规格/质量审查后提交。

阶段三验收：文件目标回归 88/88、清单 28/28，CORS 修正定向 7/7、独立复审 3/3 与质量 PostgreSQL 探针 4/4 通过；当前 187 文件类型及架构检查通过。追加 0016 文件用途迁移，正常备份副本原 86 表、1,665 行的原列保持；依赖仅新增 multipart 两个 Python 版本标记，旧锁文件其余字节不变。完整后端总验收继续任务六。

## 任务四：个人知识库、技能与异步操作

- [x] 正确Bearer+Scope下创建本人KB、入库、查询；本人SKILL包预检确认；operation状态/取消按应用隔离，body指定其他owner拒绝。
- [x] 知识库元数据、目录、文档、重新切片/重建走原领域作业；API来源只存服务端owner/app/key/request身份，执行阶段复核权限。
- [x] 索引和查询仍走原Run/Model/积分预占结算；接纳检查模型/费率，费用和余额按模型步骤原制度，不新增账本。
- [x] operation是原作业状态映射：partial不能报成功，cancel_requested不能报cancelled；网络断开不取消；撤权后缓存结果也过滤当前来源ACL。
- [x] 技能按个人查找，同slug跨用户独立；同Owner未授权给当前App的目标不能预检泄露/覆盖。历史revision和确认事实不能改写。
- [x] `pytest tests/core/integration/test_resource_knowledge.py tests/core/integration/test_resource_skills.py tests/core/integration/test_resource_operations.py -q`，补真实Worker/Milvus、撤Key、ACL变化、无重复消费/重复安装。
- [x] 两阶段审查后提交。

阶段四验收：27 个资源方法/路径齐备；160 项受影响检查点及后续技能元数据、fence、实际新建事实、权限/429 修正均通过，最终 15 项与冻结 38 文件逐项匹配。独立规格 3/3、质量 PostgreSQL 2/2 通过；当前 200 文件类型及架构检查通过。原 Worker/Run/Usage 用例复用，无新增迁移、依赖、队列或账本；最终全量回归继续任务六。

## 任务五：协议目录、规范与前端

- [ ] 将控制台、资源、推理路由分组，旧HTTP路径和功能字段保持；拆分旧public.py的DTO/SSE/路由，等待和账务用例仍属于application。
- [ ] 不保留两套写逻辑；其他域只在触及该域时整理。模块跨域只能public，新增api禁止访问私有ORM/SDK。
- [ ] 从实际APIRoute/DTO生成资源分组OpenAPI3.1和 `/docs/resources`，上传Schema、Bearer/Cookie、Scope、分页、If-Match及202都能被客户端读取。
- [ ] 开发接入补资源Scope和应用授权列表，Skill/KB默认“我的”过滤；文档元数据来自同一接口目录，示例走契约校验。
- [ ] 前端核心/type/lint/format及权限/个人/上传/开发接入浏览器回归；真实无Cookie客户端与现有Console读写都验证。
- [ ] 审查后提交。

## 任务六：完整验收与当前实例同步

- [ ] 全后端/受影响回归和源码hash对应；私有测试服务使用独立Redis逻辑库，避免跨queue的ARQ cron竞争。
- [ ] 正常库只读审计→完整备份→副本升级验证，核旧模型/Key/费率/积分、消息、个人数据和历史修订；未明确归属时停止发布并继续调查，不带猜测迁移正常库。
- [ ] 真实Vite→API→PG/Redis/Worker→本地协议Provider→Milvus完成文件、个人KB、个人Skill、应用授权/撤权、操作查询/取消；前端不打包。
- [ ] 依次规格与质量总审；只快进同步已验证提交，显式迁移加载schema后恢复源实例，原有57文件指纹保持。
- [ ] 原入口正常16页只读复查、旧Key推理/新资源Key隔离、原积分和财务指纹、真正JSON就绪；精确清理本次QA，不动审计/正常数据。
- [ ] 更新README/实际结构/验收结果，标明OpenAI资源兼容仍未注册，保留分支和私有证据。
