# 平台资源 API v1

资源协议基址为当前服务入口的 `/api/v1`。本机源码入口为 `http://127.0.0.1:19080`，示例使用同一入口；直接运行 API 时默认端口为 `19090`。控制台前端仍使用 Cookie 调用 `/api`，模型与智能体推理保留 `/v1`。一个 FastAPI 应用和一个 Container 统一装配三组协议。

运行当前源码后，准确目录和 DTO 以 [资源 Swagger](/docs/resources) 和 [OpenAPI 3.1](/openapi/resources-v1.json) 为准；[推理 Swagger](/docs/inference) 与 [推理规范](/openapi/inference-v1.json) 单独分组。开发接入的“API 说明”从这些实际接口加载目录、字段、响应头与 curl 示例，覆盖文件、知识库、技能和原生操作。示例 JSON 在生成时通过相同请求 DTO 校验。OpenAI Files、Vector Stores、Skills 的资源 SDK 兼容层只预留目录，未注册任何兼容资源路由。

## 密钥与个人边界

在“开发接入”中创建应用，为访问密钥选择有限操作能力和独立资源 RPM，再在“资源授权”中授权已有文件、知识库与个人技能。能力标签、说明及 RPM 范围来自后端同一注册表。Scope 签发后固定，轮换精确复制原权限；更改能力需签发新密钥。旧调用方省略 Scope 且提供模型时，保留原模型与智能体推理能力，不自动增加资源能力。

每次访问都取当前个人基础权限、Key Scope、应用授权及领域 ACL 的交集。管理员身份不会自动开放其他人的私有资源；共享资源只有明确允许委托的当前读/检索动作，公共可见也不等于可委托。同一个人拥有的两个应用也不会自动共用资源。本人未索引的知识库、已停用或已删除但可恢复的技能可以显式授权。失权或归属变化的旧绑定隐藏名称，只保留通用失效提示和 ID，可取消授权。

API 创建的资源归应用真实拥有者并自动关联当前应用。不能提交 owner、物理存储路径或模型供应商凭据。知识片段可检索不自动授予原始文件下载权限；下载必须同时具有文件 Scope、应用文件授权和当前文件 ACL。

仅管理文件、技能或知识元数据的 Key 可以没有模型。执行索引、检索以及其他有模型调用的步骤仍须允许相应 Embedding、生成或排序模型，但不会因此开放裸推理 Scope。CRUD、上传与下载按资源请求限速，不算 Token；模型调用继续使用原 Run、Worker、积分预占和账本。

## 接入步骤

先设置当前入口与 Key；为每个新的幂等 POST 生成 UUID，同一请求重试保留原键。

```bash
export ORIGIN='http://127.0.0.1:19080'
export API_KEY='你的访问密钥'
export REQUEST_ID="$(python -c 'import uuid; print(uuid.uuid4())')"
curl --fail-with-body "$ORIGIN/api/v1/files" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Idempotency-Key: $REQUEST_ID" \
  -F 'file=@./notes.txt' -F 'purpose=knowledge'
```

从响应取 `id` 为 `FILE_ID`。在分组文档按顺序使用“创建知识库”提供 `embed_model_id`，取知识库 `id` 为 `BASE_ID`；“文件入库”提交 `file_ids`，按 202 响应的 `Location` 轮询 operation，完成后调用“知识检索”。不要用知识库原始文档的 ID 替代 file ID。

技能流程使用 `purpose=skill` 上传 `SKILL.md` 或 ZIP，在“预检个人技能”提交 `file_id`，检查 `action`、目标和 `expected_version` 后用 `draft_id` 确认。返回 `id`、`current_revision_id` 和 `resource_version`；内容修订不可原地覆盖。包内 `package_version` 是标签，`resource_version` 是状态 CAS 版本，引用固定内容使用不可变 `revision_id`。脚本附件保存不等于获得执行能力。

## 传输约定

- `Authorization: Bearer …`，资源不回退到 Cookie；HTTP Bearer 的操作能力使用规范中的 `x-required-scopes`，不伪装成 OAuth。Operation 读取根据原生 kind 要求 `knowledge:read` 或 `knowledge:query`。
- multipart 只接受一个二进制 `file` 与一个 `purpose`；原文件最多 20 MiB，技能包还受 10 MiB/解压 30 MiB、200 文件及主文件 128 KiB 等原领域限制。普通 JSON 请求限额独立。
- 创建、导入、确认和有计量检索要求 UUID `Idempotency-Key`。相同应用/操作/键和内容返回原结果，内容变化为 409。请求身份永久保留，响应快照 24 小时；过期重放返回 409 及当前允许的原结果引用，不重新执行。Operation GET 无此 24 小时窗口，仍复核当前 Scope、应用与 ACL。
- 列表为 `items/next_cursor/has_more`，默认 20、最多 100；游标绑定应用和过滤条件。可变资源修改发送强版本 `ETag` 作为 `If-Match`，缺少为 428、陈旧为 412。资源文件元数据、内容下载（含 Range）和变更响应使用相同的 `"file-{version}"` 状态版本；技能不可变原包下载的 ETag 则为内容 SHA256，不能作为技能状态版本；领域引用、幂等和在途冲突为 409。
- 异步接纳为 202 + `Location` + operation。至少隔一秒轮询；保留 `partially_succeeded`、`cancel_requested`、`interrupted`，取消接纳不等于立即停止。只查询既有原生作业，不新增队列、执行器或账本。
- 资源响应总有 `X-Request-ID`；错误为 `error.code/message/details/request_id`，429 带 `Retry-After`。下载支持单 `Range`、206 和 `Content-Range`。推理仍使用原 OpenAI 错误形状和 SSE `data: [DONE]`。

应用默认上限为 1000 个原始文件、1 GiB 逻辑空间，平台管理员可在资源授权页修改。计数来自源文件登记事实；重复绑定不重复计量，回收与解绑不释放空间，实际清理后由原文件生命周期回调释放。派生索引及技能快照纳入平台存储统计，不换算为 Token。

## 源码代理与迁移

Vite 与 Nginx 模板均需代理 `/openapi/`，避免分组 JSON 落入 SPA。源码模式的实际 Nginx 配置由外部 `CORE_SOURCE_PROXY_CONFIG` 挂载，更新仓库模板后还需在发布阶段重新生成并重载外部配置；保留共享链接日志脱敏、HMR、SSE 超时与无缓存配置。无需打包前端。

本分支最新迁移为 `0016_file_purpose`。个人技能升级先审计可确认的归属，旧内置模板生成个人副本并保留历史关联；无法唯一归属的活动引用会阻止升级，不能统一分配给管理员。源实例同步、完整备份副本升级与端到端验收由发布任务完成；本接口说明不代表正常实例已经升级。
