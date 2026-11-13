# 开发接入：访问密钥管理

应用详情的“访问密钥”提供创建、轮换、撤销和删除。管理操作使用登录后的 Console Cookie，仅由应用拥有者执行；超级管理员也不能从个人入口读取他人应用详情、读取／签发／轮换／撤销／删除他人密钥；用于资源及推理调用的 Bearer Key 不能代替 Console 登录。

## 角色操作权限

非超级管理员须启用开发接入（`platform:developer:enabled`）并具备查看权限。签发、轮换、撤销、删除 Key 分别要求 `platform:developer:key_create`、`key_rotate`、`key_revoke`、`key_delete`，不能以应用编辑权限代替。Bearer 实际调用另需 `platform:developer:invoke`，并同时检查 Scope、模型、应用授权和当前资源权限。参见[模块与操作权限](platform-module-permissions.md)。

## 删除密钥

```http
DELETE /api/developer/applications/{application_id}/keys/{key_id}
Content-Type: application/json
Cookie: zhiheng_session=<登录会话>
```

两个 ID 均使用服务器返回的 UUID。请求体可以省略，无需填写原因；浏览器写入须来自受信任来源。成功返回 `204 No Content`，无响应正文。

- 有效、轮换宽限期、已撤销、已过期的密钥均可删除。
- 删除在同一事务中将密钥撤销、清除轮换宽限并记录删除时间；提交后原 Bearer 不再能发起请求，管理列表／总数不包含该条记录，详情、轮换、撤销入口返回 404。
- 当前调用者仍有该应用管理权限时，重复删除同一密钥返回 204，不重写删除时间。应用或调用者已不可用时仍按当前权限拒绝，不因历史删除结果绕过鉴权。
- 原记录及 ID 保留，历史调用、积分、金额账单、任务来源继续关联原 Key。删除不自动抹除费用，也不释放未知用量的预占；原有核对流程仍可结算。
- 已接纳但尚未开始的模型执行会复核原 Key 并被拒绝；已经发起的上游调用按原身份及实际用量结算。轮换与删除通过相同的行锁顺序串行化；删除原 Key 不删除另一次已完成轮换签发的新 Key。

没有登录或误用 Bearer 返回 401；非应用拥有者（包括超级管理员）、应用与 Key 不匹配、目标不存在返回 404。本人缺少当前操作权限返回 403；额外请求字段或无效 UUID 返回 422；不可信浏览器来源返回 403。

本接口属于控制台管理协议，不增加资源 API 的 Scope，也不改变 `/api/v1` 与 `/v1` 的调用方式。

平台额度与启停在独立的[应用治理入口](application-governance.md)管理，不开放个人应用内容或密钥管理例外。
