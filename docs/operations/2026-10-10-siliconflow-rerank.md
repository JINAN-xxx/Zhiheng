# 硅基流动重排序模型接入兼容

用户接入 `BAAI/bge-reranker-v2-m3` 失败。检查当前运行服务及审计记录 114～116，三次连接测试均返回 `success=false`、`failure_reason=network`。

## 原因

硅基流动连接在平台中按 OpenAI 兼容协议管理，但 LiteLLM 的重排序调用不支持 `custom_llm_provider=openai`，在访问供应商前就返回 `Unsupported provider: openai`。错误被包装为 `APIConnectionError`，原诊断因此误报网络故障。

正式接入链路还存在其他兼容差异：

- 前端创建引擎部署时没有提交选定的模型类型。
- 硅基流动此模型需要文本数组；平台原先发送 `[{"text": "..."}]`。
- `return_documents=false` 时供应商返回 `document:null`，LiteLLM Cohere 响应模型不接受这个值。
- 真实返回包含有效的输入 Token 数和 `search_units=0`；平台既有领域模型仅接受正检索单元或缺省值。

接口要求参考 [硅基流动官方文档](https://docs.siliconflow.cn/docs/api/rerank-post)，上述差异另通过当前网关与已保存连接验证。

## 修改

前端接入流程将 `modelType` 传入部署创建接口，后端写入 LiteLLM `model_info.mode`。原客户端可以继续省略此字段。

新增 `rerank_compatibility.py`，仅对 `https://api.siliconflow.cn/v1`、重排序类型和 OpenAI 兼容连接，将内部调用映射到 Cohere 重排序协议及完整 `/v1/rerank` 地址。内部元信息保存原协议身份，列表、凭据连接解析和更换连接时还原；用户仍选择“硅基流动”，不需要了解内部适配器，也不修改共享凭据。其他主机、协议、端口和模型类型不触发转换。

运行时重排序统一发送文本数组并要求上游返回非空文档结构。上游文档内容不会转发给调用者；公开接口仍根据调用者的 `return_documents` 选择是否从原始请求重建结果正文。

精确整数 `search_units=0` 按缺少有效正单位测量处理，保留真实输入 Token。现有按检索单元的结算规则会估算 1 单位并标记 `cost_estimated=true`；按 Token 计费继续使用实际 Token 数。未修改费率，也未将估算单位宣称为供应商实测值。负数、布尔、字符串和浮点单位仍拒绝。

诊断优先将 `Unsupported provider:` 归为请求/协议配置错误，不再误报网络故障。

## 验证

- 新兼容用例修改前 7 失败、1 通过；额外的零单位用例也先复现失败。
- 相关后端回归 **476 passed**；1 条既有测试事件循环缓存重置提示。Ruff 与格式检查通过。
- 前端插件测试 **56 个文件通过**，内含 Node 测试 9/9；Docker 模式构建成功。
- 模型接入浏览器回归在独立构建预览和实际 `12580` 静态服务验证，覆盖三种模型类型、已有/新建连接、发布重试、响应丢失恢复及过期请求。业务 API 使用隔离模拟数据。
- 候选后端通过真实服务端连接测试，约 **207.23 ms**。以“fruit”对两条合成文本重排序，`return_documents` 两种调用都正确返回索引 0，并保留 **21 个输入 Token**。
- 验证了真实网关创建/列出部署、复用凭据、更换连接、修改描述及运行适配器调用。临时测试部署全部删除，没有创建用户的正式平台模型。
- 独立代码审查及补充用量审查未发现阻断或重要问题。

本地扩大回归时发现测试环境缺少已声明的 Lua 测试依赖 `lupa==2.8`，补齐后配额相关测试通过；运行镜像依赖未因此变化。

## 部署

工作区：`/home/ja/project/zhiheng`。基于已运行的聊天取消修复镜像，仅覆盖本次后端文件，部署为 `zhiheng-backend:rerank-fix-20261010-3b345a68fb5c`；`.env` 后端镜像项已同步。无数据库迁移，LiteLLM 容器无需更换。

前端静态文件同步至当前 Nginx 绑定的 `dist`，先资源后 HTML；浏览器验收后清理旧静态资源和临时构建副本。没有新增离线镜像归档，保留的旧发布包未更新。

上线确认后端健康、运行文件哈希正确、公开 OpenAPI 接受部署 `modelType`、本机前端入口与构建一致、公网页面返回 HTTP 200。

小型记录：`/home/ja/.local/state/zhiheng-model-validation/rerank-20261010-1v5mzmn_/`。环境备份为私有文件，不能输出或提交。

用户刷新页面后，以“重排序”类型、模型名 `BAAI/bge-reranker-v2-m3`、服务地址 `https://api.siliconflow.cn/v1` 重新接入即可，也可复用已有硅基流动连接。

## 后续正式接入

用户随后反馈模型目录中没有该模型。确认前述阶段仅修复和测试了接入链路，目录当时仍只有聊天、向量两个正式模型。

已按用户意图复用现有硅基流动连接，通过现有服务层完成正式部署、目录创建、运行时绑定和发布：

- 显示名称：`bge-reranker-v2-m3`；模型代码：`baai-bge-reranker-v2-m3`。
- 平台模型 ID：`mdl_9f982aa2f5704c3f855b76437b601fdf`。
- 类型：`rerank`；发布状态与绑定状态均为 `active`。
- 网关别名：`siliconflow-bge-reranker-v2-m3`。
- 沿用发布流程同步内部运行密钥的具体模型权限，没有新建或改写上游凭据。

正式模型的类型化测试通过，测得 94 ms；随后通过平台模型 ID 和平台运行密钥执行双文档重排序，结果正确。模型目录服务查询返回 3 条记录，包含该模型。操作记录和目录响应保存在前述目录的 `formal-onboarding.log`、`formal-catalog.json`；维护操作者记录为 `codex-maintenance`。
