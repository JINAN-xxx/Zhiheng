# 知识库邀请共享

知识库共享仅支持邀请链接。所有者在库内“共享”生成一次性邀请；接收者直接打开链接，或从“知识库 → 加入知识库”粘贴本站链接，登录后预览并明确接受，获得本库资料的检索、查看和下载权限。邀请不改变账号角色，也不授予管理知识库、资料或平台的权限。

每条链接默认 24 小时有效，由首位登录并确认的账号领取。已有成员进入不消耗其他人的邀请名额。相同账号重试不会重复授权，并发领取仅有一个新成员成功。所有者可重新复制有效链接、撤销待领取邀请或移除已加入成员。撤销链接不移除已加入成员，移除成员会使该库此前未领取的邀请失效。

普通用户与管理员均通过邀请接受流程新增共享成员。共享弹窗只展示本库相关成员，不搜索平台账号，不提供全员共享、手动选人或编辑访问范围。

## 会话 API

以下路由均要求 Cookie 登录，仅所有者可以管理邀请和成员。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | /api/knowledge/bases/{id}/invitations | 生成邀请，提交 expected_acl_version、client_request_id |
| GET | /api/knowledge/bases/{id}/invitations | 最近 50 条邀请状态，不含令牌 |
| GET | /api/knowledge/bases/{id}/invitations/{invitation_id}/link | 重新取得有效邀请链接 |
| POST | /api/knowledge/bases/{id}/invitations/{invitation_id}/revoke | 撤销邀请，空 JSON 对象 |
| GET | /api/knowledge/bases/{id}/members | 仅本库已授权成员 |
| DELETE | /api/knowledge/bases/{id}/members/{user_id} | 移除成员，提交 expected_acl_version |
| POST | /api/knowledge/invitations/preview | 登录后预览，JSON 提交 token |
| POST | /api/knowledge/invitations/accept | 明确接受，JSON 提交 token |
| GET | /api/knowledge/bases/{id}/acl | 所有者查看现有授权及版本 |
| POST | /api/knowledge/bases/{id}/sharing/cleanup | 明确整理历史规则，仅接收 expected_acl_version |

旧 PATCH /api/knowledge/bases/{id}/acl 和 GET /api/knowledge/bases/{id}/users 已移除。创建知识库的 access 参数只接受 private；数据库约束也禁止 public。历史整理只移除拒绝、到期或限时规则，保留无到期时间的已有成员，不接受目标用户 UUID 或访问范围。需要恢复的成员应重新通过邀请加入。

## 升级与数据

迁移 0023_knowledge_link_only 将旧全员共享的知识库改为 private，保留已有成员、邀请记录、资料、索引、用户和账务数据。仅被收回全员共享的库增加 ACL 版本及邀请失效标记；原私有库的有效邀请保持有效。未被单独授权的账号失去全员访问权。降级不会自动恢复全员共享。

旧库仍须由所有者预览、确认资料权限迁移。若含旧拒绝或到期授权，界面会明确列出待移除规则，经确认整理后重新预览。不会将到期授权静默转换为永久成员。

令牌放在 /knowledge/join 的 URL fragment 中，HTTP URL 不含令牌，预览及接受通过 POST JSON 传递。手动粘贴入口校验本站地址和链接格式，查看邀请不会自动加入。数据库只保存令牌哈希，邀请响应禁止缓存。无效、过期、撤销、被他人领取或所有者停用的邀请统一返回 404。
