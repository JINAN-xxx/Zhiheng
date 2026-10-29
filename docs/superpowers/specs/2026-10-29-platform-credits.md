# 平台积分一期

用户已确认平台积分方案并要求实施。每用户独立积分账户，管理员手动发放、追加、回收，长期有效；所有实际模型调用统一预占/结算。默认新账户零积分，回收只处理未使用且未预占部分。历史 Token/金额保留原单位，不补扣、不把旧金额伪装为积分。

## 账务与切换

- 新增内置 `billing` 模块拥有积分账户、积分价格、预占和不可删改流水。平台调用改用 billing 统一用量服务；旧 developer 用量服务作为注入的历史计量端保留，不继续添加积分业务规则，不跨模块操作对方 ORM。
- 新账表默认积分计费未启用，原有调用继续使用旧规则。价格、用户余额准备好后，超级管理员在用量页面显式启用；开关有版本、幂等和审计。普通积分管理员不能切换全平台计费模式。
- 积分使用 Decimal（内部固定18位小数），JSON输入输出为字符串。模型输入/输出/缓存积分单价以每百万 Token 配置；重排序按检索单元。免费需显式标记，全部零价但未标免费不允许。未配置积分价的模型在启用后拒绝新调用。
- 请求锁定价格快照及版本；输入估算与最大输出计算预占，完成按归一化实际计量结算。缓存属于输入，避免重复计算；未知用量保留预占并提供管理员核对入口。取消/失败仅确认未执行时全释放；已发生用量照常结算。
- 唯一请求/运行约束和数据库锁保证并发不超发、不重复扣款。实际消耗超过预估仍记真实消耗，账户可显示超额并阻止新调用，不截断用量。
- 新积分模式取消旧终身 Token 上限作为消费主闸，保留 Token 实际统计和现有单次/速率/并发护栏。API Key共享应用所有者积分，新增可选月度积分上限，不能创建Key获得额外余额；旧金额限制保留原单位。
- 用户余额、积分模式开关和旧账不因数据迁移自动充值或重算。生产先增加账表和管理功能，积分扣减保持关闭，真实发放与启用由管理员在页面执行。

## 核心公开契约

`CreditService` 提供 `state/set_state`、`summary/summaries`、`adjust`（grant/reclaim）、`set_account_state`、`rates/create_rate/quote`、`reserve/require_reservation/settle/reconcile`、`ledger/reservations`。写操作均使用调用者、client_request_id、reason；状态/核对操作另带 expected_version。读写权限为 `ai:credits:read/write`，费率使用原 `ai:rates:read/write`，本人查询默认可用。

- state：`{enabled, version}`，默认 false/0。
- summary：`{user_id,enabled,version,allocated_points,reclaimed_points,used_points,reserved_points,available_points}`，金额为字符串，缺账户返回零余额/version0。
- adjustment：`{kind:'grant'|'reclaim',points,reason,client_request_id}`。非正数、浮点、NaN/Infinity拒绝；同幂等ID不同载荷409。
- account state：`{enabled,expected_version,reason,client_request_id}`。暂停阻止新的预占，已有消费仍结算。
- point rate：`{billing_mode:'token'|'search_unit',is_free,input_points,output_points,cache_points,search_points}`，前三项单价按百万Token，search按单元；返回 `id,model_id,version,created_at`及上述字段。
- reserve：`session,user_id,run_id,model_id,source,operation,estimated_input_tokens,max_output_tokens,estimated_search_units,key_id,key_limit`，底层模型调用每步一条，父任务只聚合。
- ledger：`id,user_id,operator_id,kind,amount,available_delta,reserved_delta,available_after,reserved_after,run_id,reason,created_at`，分页 `{items,total,page,page_size}`。
- reservation：`run_id,user_id,model_id,source,status,version,reserved_points,charged_points,rate_version,created_at`，状态 reserved/unknown/settled，收费快照保留。

## HTTP与界面

新增 `/api/billing`：

- GET `/state`、PATCH `/state`（仅超级管理员）；开关请求 `{enabled,expected_version,reason,client_request_id}`。
- GET `/me`、`/me/ledger`、`/me/usage`，分页参数 page/page_size。
- GET `/accounts?search=&page=&page_size=`，返回summary加username/display_name/is_active；经 identity 公开目录组装，不能直接读identity ORM。
- POST `/accounts/{user_id}/adjust`、PATCH `/accounts/{user_id}/state`、GET对应 `/ledger` 和 `/usage`。
- GET/POST `/models/{model_id}/rates`：管理价格历史与新版本；GET `/models/{model_id}/quote` 给已发布模型提供公开积分价格，其他需管理读取权限。
- POST `/usage/{run_id}/reconcile`：仅 credits write，要求已unknown、expected_version、client_request_id、reason和可确认的 input/output/cache/search 用量或 rejected_before_execution。

复用“用量与调用”：普通用户看我的积分/消费与历史Token；有积分管理权限者看用户账户、发放/回收/暂停与待核对。模型费率弹窗增加积分计价，原金额版本作为历史保留，不混单位。新Key可配置月度积分上限；页面沿用白色/系统字体/蓝紫，不新增重复主导航。

## 一期验收

100积分账户，输入100/输出500积分每百万Token：估算输入1000、最大输出1000预占0.6；实际输入1000输出200消费0.2、释放0.4，剩99.8。需验证管理员发放与回收幂等、并发超支、免费/缺价、未知核对、费率变更、API Key共享与月限、全部模型入口、旧账迁移、权限/所有权和真实UI/Worker/本地供应商闭环。正式账户不预填测试价格/积分，不调用收费供应商。
