# 对话消息操作接入计划

用户已要求移除对话体验中的“聊天/工作”切换，并接通消息操作栏的重新生成、赞踩、朗读和分享。沿用当前控制台设计，正式业务状态进入后端；使用现有模型接入、运行、配额和用量治理。

## 行为与边界

- 对话欢迎页移除模式切换，工作功能仍从主导航进入。
- 重新生成仅针对最新一轮的回复，创建新的受治理 Run，保留原回复和版本；浏览旧版本不暗中重写后续对话。复用原问题/模型/技能/文件/知识库选择，按当前权限与资源可用性重新校验。停止、失败、断线与未知接纳不能重复扣费或丢失旧回复。
- 赞/踩互斥，可清除，按用户与消息持久保存；PUT提交绝对值，服务端执行归属校验。
- 朗读使用浏览器语音能力，提供停止与切换消息互斥，切页/卸载停止；不支持或播放失败给出真实原因。
- 分享选中的回复，可选择附带提问，先预览再显式创建。默认7天、最长30天，随机令牌仅存哈希、可撤销；公开只返回冻结文本，不携带附件、知识原文链接、隐藏推理或身份元数据。对方需能访问当前平台部署地址。
- 工作消息可复用复制/朗读；聊天专属的重新生成、反馈、分享通过聊天模块显式提供能力，不把聊天HTTP写入shared组件。

## 持久化与协议约定

统一迁移 `0009_message_actions` 由主任务维护。chat_messages增加 nullable reply_to_message_id、自1起generation_index及request_context JSONB（原选择的非敏感标识）；新增反馈和分享表由聊天模块拥有。原行/原输出保留，API/worker不自动建表。

消息DTO增加 reply_to_message_id、generation_index、is_complete、feedback，可兼容旧夹具缺省。重新生成以原提问作为同一轮，多份回复由版本选择器展示；新Run先登记占位回复，保证刷新可找到运行并恢复。

- POST `/api/chat/conversations/{conversation_id}/messages/{message_id}/regenerate`：body含client_request_id；返回既有RunSnapshot或知识准备契约。
- PUT `/api/chat/messages/{message_id}/feedback`：body.value为up/down/null；返回message_id/value。
- GET `/api/chat/conversations/{conversation_id}/feedback`：当前用户的反馈列表；消息GET也补回feedback，避免前端逐条请求。
- GET `/api/chat/messages/{message_id}/share-preview`：include_question查询参数，返回question/answer/content_hash。
- POST `/api/chat/messages/{message_id}/shares`：include_question、expires_in_days、expected_content_hash；返回一次可复制token及元数据。
- GET `/api/chat/messages/{message_id}/shares`：仅返回本人创建的分享元数据，不返回令牌。
- DELETE `/api/chat/shares/{share_id}`：归属校验后撤销，幂等。
- GET `/api/shared/messages/{token}`：匿名读取未过期、未撤销且源会话未删除的文本快照。
- 前端公开路由 `/share/:token`，与登录页的已登录跳转规则分别处理。

## 分工与实现

- [x] 后端重新生成：chat/service.py、orm.py、contracts.py、public.py、application/chat_runs.py及必要既有知识准备/RunStore公开方法、chat HTTP路由。保留原正常发送与知识接纳不变量，补真实PG/本地provider测试。
- [x] 后端反馈/分享：聊天模块内独立contracts/service/orm与独立HTTP路由文件；不写bootstrap、迁移、原chat/service或原chat_runs。补权限、快照、过期/撤销、并发与幂等测试。
- [x] 前端：MessageActions保持展示与事件职责；聊天api/composable承接真实操作，版本与失败恢复，分享预览/链接管理/公开页；独立浏览器语音协调器，完善可访问名称和状态。
- [x] 主任务：统一bootstrap/routes/迁移/依赖检查注册与契约接线；原欢迎页切换已删除，更新导航回归。
- [x] 独立规格与质量审查，关闭实际问题。
- [x] 现有Python 3.12主运行环境完成适当单元、PG/Redis/本地模型服务集成；前端core/type/lint/format/build及构建后浏览器验证。禁止用跳过或UI假状态代替验收。
- [x] 隔离实例先验证迁移与旧数据保留，随后备份并更新正常实例的相关服务；保留现用镜像与配置。原55份未提交优化不进入本次提交。

测试脚本、运行配置、失败记录和私密证据放在工作区外 `message-actions/20261028` 验收目录。用户未要求接通云端MinerU，不扩展模型SDK或下载其他模型。

用户后续明确优先源码运行，本轮最终以源码挂载/Vite热更新/API重载启用，未把反复打包作为运行前提。真实数据库备份、0009迁移、原数据指纹与正常入口验证已完成；结果见[验收记录](../verification/2026-10-28-message-actions.md)。
