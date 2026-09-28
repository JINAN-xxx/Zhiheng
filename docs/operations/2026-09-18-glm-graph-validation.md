# GLM 5.3 原三文档图谱验证

结论：当前 GLM 5.3 配置未通过这组图谱构建验证，3 个文档均在实体抽取阶段触发240秒模型调用超时。短请求仍可成功，不能据此归为模型连接或凭据失效；本次没有验证关闭思考或修改其他调用参数后的行为。

## 配置与结果

使用线上镜像 `zhiheng-backend-pg:graph-build-reliability-20260918`、已发布模型 `zai-org-glm-5.3`（`mdl_4b55ba2984df4eb484759a9807c9f17e`）、原 Qwen 向量模型及相同文档切片设置。图谱请求沿用16384输出token上限和240秒超时，没有对 GLM 套用 DeepSeek 的关闭思考扩展。每次模型调用断言了目标平台模型ID。

| 文档 | 结果 |
| --- | --- |
| hosts.txt | 实体抽取超时，`KNOWLEDGE_GRAPH_LLM_TIMEOUT` |
| 服务器到货款支付申请.docx | 实体抽取超时，`KNOWLEDGE_GRAPH_LLM_TIMEOUT`；含初始化约248.27秒 |
| 知识库问答.docx | 首批切片抽取超时，`KNOWLEDGE_GRAPH_LLM_TIMEOUT`；约240.07秒 |

构建未成功，未进行图谱检索和持久化成功性断言。没有可用图谱结果。另用正常 ModelService 对同一 GLM 发起128输出token的短请求，2.45秒得到 HTTP 200、2个回复字符和506个思考字符，finish_reason为stop。这个对照只能证明当时短调用正常，尚不能确定长抽取超时的内部原因。

## 隔离与清理

测试使用独立workspace，原知识库目录只读挂载。首个文档失败后清理第一组测试图谱，另外两个文档在第二组独立workspace继续验证；所有测试workspace的 PostgreSQL 图节点及 Milvus 集合均删除并确认不存在。

原图谱仍使用 DeepSeek，状态completed；原文档、向量索引和图谱任务状态未修改。验证期间检测到用户对知识库名称的独立修改，保持该修改，不恢复元数据快照。

记录位于 `data/agent-releases/20260918-glm-graph-validation/`，包括安全结果JSON和日志。临时环境文件验证后清除。此次未更改应用代码、模型配置或部署。
