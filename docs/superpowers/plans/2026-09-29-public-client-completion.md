# 剩余四个前端公共客户端实施计划

**目标：** 完成已确认的前端公共边界第 2–5 项，用户无需再逐项确认。后端应用查询、模型引用和费率归属仍独立处理。

**架构：** 模块 public 文件拥有跨插件使用的 HTTP 请求定义，旧 api 文件直接 re-export 保持兼容。公共入口明确导出所需的同模块数据归一化/展示辅助函数；这些辅助实现继续归所属模块所有，不复制逻辑。对方插件仅引用 public，不再引用 api/utils。

## 文件与契约

| 新入口 | 拥有的请求 | 公开的相关能力 |
| --- | --- | --- |
| `plugins/agent_extension/public/skills.js` | `listAvailableSkills` | 技能选项归一化、输入触发符识别和移除 |
| `plugins/ai/public/resourceSelection.js` | `listPlatformModels`、`listAvailableKnowledgeBases` | 模型可调用判定、模型分页/知识库归一化、分页收集 |
| `plugins/ai/public/applications.js` | `listApplications`、`createApplication` | 应用及应用分页归一化 |
| `plugins/ai/public/developerKeys.js` | 密钥列表、创建、轮换、撤销 | 密钥安全展示与新建结果提取、状态、API 地址和复制辅助函数 |

公共入口是前端模块间代码契约，不新增网络接口或开放权限。保留 URL、字段、Promise/错误、路径校验、重复提交标记与消费者状态机。管理模块专有操作继续在原 API 文件；公共文件不转发到旧 API。SkillPicker/Chat 输入框、AgentEditor、AgentApiDrawer 接入相应入口。

## 实施与验收

- [x] 保存当前前态与运行 dist 哈希，确认已有 52 文件回归基线。
- [x] 新行为测试先确认缺少四个公共入口失败；真实 ESM 加载替换共享 request，验证新旧导出身份、请求/响应/错误透传、路径校验、分页与选项归一化、新密钥仅在签发结果提取。
- [x] 四个公共入口依次实现，旧 API 兼容转发，消费组件仅改导入。
- [x] 校验跨插件静态导入只指向对方 public；共享实现与旧管理 API 测试保留。
- [x] 全前端测试、隔离构建、技能/Agent/应用密钥相关浏览器检查通过。
- [x] 独立只读审查契约、权限判断与密钥状态机保留情况。
- [x] 更新五步计划与架构状态，明确前端这组完成、后端及质量收尾尚未完成。

工作区 `/home/ja/project/zhiheng`，分支 `fix/platform-integration-review`。本轮前态位于 `/tmp/zhiheng-public-clients-n974ihwj/before`。验证不覆盖正在服务的 dist；不提交、推送或部署。
