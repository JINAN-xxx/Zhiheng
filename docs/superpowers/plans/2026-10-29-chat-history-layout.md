# 对话历史弹窗与模型生成设置

用户确认侧栏突然展开会挤压聊天，要求改进历史访问；同时明确右上角设置要配置模型提示词、温度和输出长度。

## 最终界面

- 历史记录使用居中弹窗，工具栏入口名“历史记录”，弹窗标题“历史记录”；复用搜索、最近/已归档、重命名/归档/删除。选择会话后关闭弹窗，聊天主区位置和宽度不变。取消桌面侧栏和展开偏好，不新建二级页面。
- “新建对话”常驻工具栏，设置齿轮真正打开“对话设置”面板，提示词/温度/输出长度均实际影响后续模型请求。当前消息的重新生成使用原参数。

## 参数与数据契约

- POST /api/chat/conversations/{id}/runs 在原字段外增加 system_prompt:string默认空（UTF-8、无NUL、最多32000字符），temperature:number|null默认null（有限数、0至2、拒绝bool），max_output_tokens:integer|null默认null（1至131072且不得超过当前模型目录上限）。空值表示模型默认；非chat模型遇到非默认生成设置应明确拒绝。
- 三项同样进入 ordinary/附件/技能/knowledge 准备与执行路径、冻结Run的ModelRequest和配额预留。自定义system提示词先于选中技能/知识系统上下文；新增提示词字节计入现有上下文和配额限制。binding.options须保留其他选项，仅覆盖显式温度。
- 默认参数保持旧fingerprint字节不变，非默认参数进入指纹；同key换参数必须409。知识准备的冻结context包含参数，断线回放保留；旧请求/消息兼容。
- ChatRequestContext与新消息快照保存用户原始选择，冻结Run的ModelRequest保存实际绑定；重新生成沿用原选择并重新校验当前模型权限与上限，空值按当前目录默认解析，不能用UI新设置覆盖历史参数。无参数旧context仍按旧规则恢复。
- MessageInfo增加 generation_config?: {model_id,system_prompt,temperature,max_output_tokens}|null，仅在真实已有快照时返回；model_id供前端切回该对话时恢复原模型，提示词只在有权读取的本人的消息接口返回，分享接口不得暴露它。默认/旧消息可null，不从生成的技能/检索文本伪造用户提示词。
- 前端发送意图将所有非默认设置冻结，重试用原请求key和参数；修改设置生成新intent。设置不写localStorage/IndexedDB，避免把自定义提示词持久留在浏览器；切会话从新消息generation_config恢复（旧消息默认），新建对话重置；离开页面按原生命周期清理。

## 分工与验证

- 根：ChatView历史弹窗、参数面板挂载/工具栏接线、实际API/worker本地模型服务验证、正常实例只读核对、文档与最终提交；保护原57份优化。
- backend：仅在隔离worktree实现api/routes/chat.py、application/chat_runs.py、modules/chat的contracts/service/public及必要knowledge准备公开接线，自己的真实PG/provider测试；不改CLI/bootstrap/运行依赖/迁移。
- frontend generation：隔离worktree内新增ChatGenerationSettingsDialog及设置类型helper，chat API/SendIntent/workspace参数冻结/恢复，自己的core/browser测试；不改ChatView（根接线），不动历史/通用测试。
- history tests：根工作区下更新历史相关浏览器fixture与用例，显式点击历史弹窗、选择即关闭；删除上轮展开偏好初始化与侧栏假设，新布局覆盖弹窗不挤压、消息草稿保持、搜索/归档和新建入口。不改产品源代码或generation测试。
- 验证真实参数出现在受治理本地HTTP模型请求、预留长度正确、指纹/重试/重新生成/知识恢复兼容；前端类型/规范与相关回归；不打包、不升级SDK、不调用收费模型。预计不需要DDL，沿用现有request_context JSON。
