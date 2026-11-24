# 正式助手对话的附件

对话体验与正式助手附件支持从混合 PDF 提取可读正文。图片页未执行 OCR 时，历史附件明确显示未识别页及已提取正文页数，并将同一说明附在模型的用户输入中；全扫描件仍要求 OCR。附件摘要增加可选的 `parse_warning`，旧历史默认空字符串。该策略使用独立的 `native-3-attachments` 缓存，知识库与存储解析仍使用原完整解析策略，不能复用部分正文缓存。

本地解析进程保留 512 MiB 内存、20 MiB 文件、500 页和文本输出等边界；CPU 软／硬时限为 30／35 秒，父进程默认等待上限为 45 秒。CPU 时限终止返回明确的解析超时，不再混同文件损坏。

正式助手会话支持文档和模型支持的图片。附件属于当前用户的会话，不自动加入知识库。
当前新增范围是 `/agents/{id}/chat` 和对应 Console Cookie 会话接口；配置调试与对外 Agent API 保持原文本协议。

- POST `/api/agents/{agent_id}/conversations/attachments?filename=...`：原始文件字节，Content-Type application/octet-stream，返回 FileInfo。
- POST `/api/agents/{agent_id}/conversations/{conversation_id}/runs`：原有 message/client_request_id 增加可选 file_ids，默认空数组，旧文本请求兼容。无文字但有文件时使用“请分析附件。”。
- GET 会话详情：turns 和 preparing_message 增加 attachments 元数据，旧记录为空数组。
- GET `/api/agents/{agent_id}/conversations/{conversation_id}/attachments/{file_id}/download`：校验本人会话、已关联附件和当前文件权限。

上传与发送需要 platform:agents:use，历史下载需要 platform:agents:read。无需扩展 Chat/全局 Files 权限；其他人的私有会话及文件仍拒绝。图片按助手实际模型的 vision 能力校验。
沿用单个文件20MiB、每条消息总计50MB和现有格式规则。解析在锁外执行，发送前和执行前复核源文件；SDK历史的附件也检查当前权限，失权后禁止使用历史内容继续回答。

重复提交使用同一请求UUID及原附件ID；改变附件列表会产生幂等冲突。历史消息保存附件摘要，源文件和解析产物受会话引用保护；删除会话或助手释放相应引用，未发送上传沿用24小时临时生命周期。

0041_assistant_attachments 只为 assistant_turns 增加默认空数组的附件列，原历史及账务不重写。存在附件历史时拒绝降级，避免丢失记录。

`0042_workspace_merge` 将助手附件与工作空间迁移汇合。当前迁移版本与升级步骤见[部署说明](../deployment.md)。
