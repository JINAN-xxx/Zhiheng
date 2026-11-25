# 前端控制器与应用组合

页面按实际的资源、命令和观察职责组合模块本地 composable。组件负责渲染、props/events 和必要的展示投影；协议与解析在模块 api，跨组件状态由模块 workspace 组合。shared 保留 Run 协议、HTTP 请求所有权和基础值类型，不承载任务、助手或模型配置规则。

## 模块职责

| 模块 | 所有者 | 职责 |
|---|---|---|
| 智能助手 | useAssistantWorkspace | 路由、会话列表/详情/旧页、身份生命周期和页面组合 |
| 智能助手 | useAssistantCommands | 创建会话、发送、持久幂等意图和未知提交恢复 |
| 智能助手 | useAssistantRunObservation | Run/preparation 投影、快照版本接受、订阅/恢复与停止交接 |
| 知识库 | useKnowledgeLocation | 资料、检索和图谱路由，校验并同步当前目录 |
| 知识库 | useKnowledgeJobObservation | 构建任务轮询、终态同步、退避和旧响应隔离 |
| Work | store | 唯一 provide/inject workspace，明确装配任务、命令和观察；身份/权限变化使旧上下文失效 |
| Work | composables/tasks | 列表、详情、可用成员资源与 task version 接受，拒绝旧详情覆盖新版本 |
| Work | composables/turns | 准入意图、taskLocks、CAS、成员/任务命令和批次的任务身份 |
| Work | composables/runObservation | Work scope 下的 Run/preparation 观察、投影、取消去重和恢复 |
| 模型 | useModelAccess | 当前主体的具体能力投影与试用失权状态 |
| 模型 | useModelCatalog | 目录/连接读取、行操作、详情选中、保存后失效旧列表与行错误 |
| 模型 | useModelConfiguration | 模型配置表单、容量/依据校验和保存生命周期 |
| 模型 | useConnectionConfiguration | 凭据配置表单、原版本编辑及 secret 清理的独立生命周期 |
| 模型 | useModelConnectionTest | 显式轻量连接测试的准入、观察和结果 |
| 模型 | useModelTrial | 试用提交意图、未知受理恢复、运行观察/取消及主体边界 |

ModelsView 明确组合这些职责。原 useModelDirectory 混合控制器已删除，不保留转发空壳。模型和助手 api 中无业务含义的共享 Run 转发已移除，调用方直接导入 shared/runs；Work 继续通过自己的 task/run 授权路由绑定 followRun。

## 观察与命令

SSE 解码和 acceptSnapshot/followRun 位于 shared/stream，通用 Run HTTP 绑定位于 shared/runs。业务层接受快照时同时检查资源身份和上下文 ticket；取消响应、轮询和详情回读仍经过版本接受，不以旧取消响应重新开始较早的观察。

preparation 与 Run 有业务交接：停止 preparation 时若服务端已受理出 Run，要继续停止该 Run。并发停止共用同一个操作；响应丢失保留未确认停止意图，恢复时先确认原状态，不再次提交付费请求。详情回读要保留本地已接受的较新 Run 状态。

助手发送的待确认记录只保留账号、会话和请求标识。刷新后根据原请求查询受理状态，未查到时允许重新输入并沿用原标识。消息正文和附件信息仅留在当前页面内存。

撤销本地订阅只结束浏览器观察，不意味着服务端任务已经停止。页面切换、身份/权限变化、显式停止和恢复各有明确的所有者与失效路径。命令在发送时再次检查对应能力，不依赖按钮是否可见。

Work 批量添加成员在批次入口固定 task ID、ticket 与版本；每轮检查该任务仍属于原上下文，后续操作不会跟随 UI 新切换的任务。任务详情的 version 与 Run 的 attempt/revision/projection_seq 分别接受，各自保持其事实来源。

凭据与模型配置拥有不同的 RequestScope、saving 和表单错误。旧凭据请求的 finally 仅清理自己仍有效的上下文，不能擦除重开表单的新密钥。模型容量依据的校验只属于模型配置。主体更换时清空目录、表单与敏感输入；试用保留同一主体下尚未确认的原幂等意图，以便核实受理结果。

## 应用资源与导航

每个模块的 resourcePreview 从自己的 api 生成小型资源预览，负责其可用性过滤、字段投影和资源链接；公开入口通过 public.ts 导出。shared/resourcePreview 只定义 keyword/limit、条目和页的值类型。应用层 resourceCatalog 只装配模块公开预览、权限选择与独立失败状态，不解释知识库或技能的私有 DTO。

资源组合传入读取时的主体快照。Overview 在身份/权限变化时撤销旧请求、清空旧资源并按新范围重读；晚到的旧结果不能恢复被撤销的资源。单项服务失败显示不可用与原错误，数量保持未知，不冒充零。

主导航的名称、路径和权限来自 shared/platformAccess 的模块元数据。app/navigation 只组织应用、资源、开发与用量三组；图标与折叠偏好由侧栏负责。保留现有顺序、账户隔离偏好、当前分组展开和移动端键盘可达性。

会话布局由路由声明，应用壳统一读取。后台装配存储页路由、旧链接与缓存接口，文件页按接口消费缓存。知识库组件通过本域 API 处理解析设置协议，HTTP 边界规则同时检查应用壳和业务模块。

## 验证边界

核心回归使用真实 composable 与受控本地 HTTP 延迟，验证 ticket、CAS/幂等、确认状态和敏感输入；浏览器回归使用真实 Vue 页面与 HTTP/SSE 夹具。权限夹具显式授予 canonical enabled/read/action，避免靠放宽产品授权获得通过。构建后另检查资源总览、局部失败与导航交互。
