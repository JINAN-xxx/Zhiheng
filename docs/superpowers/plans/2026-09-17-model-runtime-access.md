# 模型接入自动同步调用授权

目标：修复自动生成的部署别名已存在、平台 Runtime Key 的模型白名单未同步而无法发布的问题。用户已确认修复并要求无需手填别名。

方案：发布时先查运行时可见模型；缺失时核实真实部署，只向当前后台调用密钥追加该别名，再查运行时并执行原有绑定版本及模型状态校验。保留已有授权、预算、密钥类型和其他限制，不开放通配权限。通过 PostgreSQL 事务级咨询锁串行化各工作进程的白名单更新；远程同步有超时且失败保留草稿，支持重试。

- [x] 为缺少授权的发布、同步后仍不可见和已可见模型补充失败测试。
- [x] 在 LiteLLMManagementClient 增加固定目标密钥的模型列表读取及更新，使用密钥摘要和请求正文，保持错误脱敏。
- [x] 增加 RuntimeModelAccessService、跨进程锁及发布流程注入；测试部署缺失、幂等、并发、错误响应、已有无限制密钥不被收窄。
- [x] 运行模型目录、管理客户端、控制器相关 pytest、Ruff 及原前端自动别名契约测试。
- [x] 构建增量后端镜像，发布到现有 8060 平台；使用 GLM 5.3 草稿验证自动授权及发布、运行真实短请求测试。
- [x] 核对原有模型权限保留，记录发布和验证结果，合并并清理临时工作区。

涉及文件：后端 `plugins/ai/infrastructure/litellm_management_client.py`、`plugins/ai/service/runtime_model_access_service.py`、`plugins/ai/infrastructure/model_runtime_access_provider.py`、`plugins/ai/service/model_catalog_service.py`、`plugins/ai/controller/ai_model_catalog_controller.py`；对应 `tests/plugins/ai` 测试。前端自动生成别名的现有行为保留，无需新字段。

验证命令：在后端运行 `python -m pytest -q tests/plugins/ai/test_model_catalog_service.py tests/plugins/ai/test_litellm_management_client.py tests/plugins/ai/test_ai_model_catalog_controller.py tests/plugins/ai/test_runtime_model_access_service.py`；在前端运行 `node tests/plugins/modelOnboardingContract.test.js`。线上验证使用正常 ModelCatalogService 和模型调用，不修改用户凭据、前端鉴权或验证码。

线上真实调用补充发现：共用默认参数 VO 将其他模型类型的空字段写入目录，运行时在过滤 null 前按类型校验而拒绝调用。补充修复创建/更新时仅保存非空默认参数；读取兼容已保存的已知 null 字段，仍拒绝未知字段及非空跨类型参数。新增三种类型的真实目录创建至解析测试及旧数据兼容测试，6 个复现用例先失败后通过。
