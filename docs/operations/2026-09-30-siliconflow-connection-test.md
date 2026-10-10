# 硅基流动连接测试超时修复

用户反馈接入 `Qwen/Qwen3.8-27B` 失败。所提供密钥只通过关闭回显的交互输入用于验证，没有放入源码、测试夹具、构建上下文或本记录，也未代为保存平台凭据或创建模型。

## 证据与判断

硅基模型列表与最小聊天请求均返回 HTTP 200，模型列表包含该型号；聊天验证共使用 17 个 token。经当前 LiteLLM 的测试请求也成功。原来的两次平台审计记录都为 `data.success=false`、`data.failure_reason=timeout`，不是认证失败或模型不存在。

前端连接测试继承 Axios 全局 10 秒超时，而当前 LiteLLM 健康探测上限为 60 秒。浏览器回归中将 mock 上游响应延迟 12 秒，原前端在响应前中断 XHR，复现失败；修复后同一场景通过。

这些证据确认平台有提前超时的问题，并确认服务商当前可调用，不能唯一确定原两次上游超时的原因。根据[硅基官方接口文档](https://docs.siliconflow.cn/docs/api/chat-completions-post)，`max_tokens` 不包含思考 token；本次还实测确认此型号支持 `enable_thinking=false`。

## 改动

- 仅 `testEngineConnection` 的前端等待时间调整为 120 秒；普通管理请求继续使用默认超时，敏感请求仍禁用防重复提交缓存。
- `connection_probe_policy.py` 仅对官方 `https://api.siliconflow.cn/v1`、精确型号 `Qwen/Qwen3.8-27B`、chat 模式及 OpenAI 兼容 provider 的探测增加 `extra_body.enable_thinking=false`。
- 在解析已保存连接的真实服务地址后应用策略；复制请求字典，不修改凭据、正式部署参数或其他型号的行为。

新增策略与边界测试包含新建/已保存连接、其他模型和地址、不支持模式、不变性，以及正式 deployment 不携带探测选项。相关后端回归 320 项通过，前端测试文件 55 个通过。独立审查及合成凭据异常脱敏检查通过。

## 同步与验证

复用当前后端镜像构建最小补丁，仅替换 `model_engine_service.py` 并加入 `connection_probe_policy.py`。补丁镜像为 `zhiheng-backend:probe-fix-20260930-17da38d07eee`，与原镜像共享基础层，新增约 56 KB，没有导出镜像归档。

在补丁镜像内通过实际服务类调用现有网关，真实连接测试成功，耗时约 1074 ms。随后仅更新根 `.env` 的 `PLATFORM_BACKEND_IMAGE`，保留其他持久配置，重建后端容器并平滑重载 Nginx 解析。前端静态文件在独立目录构建后同步，当前端口 12580 的 12 秒延迟浏览器检查通过。7 个常驻服务均健康，临时构建文件已清理。

本次不是将整批尚未发布的模块化后端改动替换到运行环境。当前后端是原运行版加上述两个文件的补丁；源代码中也保留相同修复。既有 `2026.09.30-rc4` 离线包未重新导出，不包含本修复及后续后台导航修改。

非敏感验证记录位于 `/home/ja/.local/state/zhiheng-model-validation/siliconflow-nwr15gl4/`，包括 `verification.json`、`backend-tests.log`、`frontend-tests.log`、`browser-before.log`、`browser-after.log`、`browser-live.log`、`rollout-result.json` 与 `frontend-deployment.json`。`environment.private.before` 为小体积原实例配置备份，权限 0600；其中不包含本次用户提供的硅基密钥，不应上传或提交。

用户刷新模型接入页面后，可使用“硅基流动”、对话类型、上述完整型号和 `https://api.siliconflow.cn/v1` 重新测试，再执行接入。代码尚未提交或推送。
