# 模型供应商用量适配

本页记录 OpenAI 兼容接口的用量归一规则。平台实际 Token 来自供应商返回的计量数据，文字长度与字节数只用于现有调用前预估，不能作为实际消费事实。

项目记账口径为 **1 积分 = 1 元人民币**。Token 费率以“积分 / 百万 Token”配置，按调用时冻结的价格结算；本功能不自动同步供应商价格。

积分计费停用时，新调用不要求价格或余额、不计算平台费用，也不预占积分或 Token。Token 只记录供应商确认的实际消耗，未知计量不会占用余额或额度；鉴权、授权、请求频率与并发规则继续生效。历史调用仍保留其原始计价快照。

## 接入范围

现有模型连接继续使用 `openai_compatible`，按实际响应字段解析，不依赖模型名称或域名判断厂商，因此自建服务和转发域名也可复用。

已核对的国内常见接入对象包括：DeepSeek、阿里百炼、火山方舟、硅基流动、智谱、Kimi、百度千帆、腾讯混元、MiniMax、阶跃星辰和讯飞星火。它们的 OpenAI 兼容对话接口提供基础用量定义，缓存、推理与流式细节取决于具体模型和接口版本。

下列规则通过本地 HTTP 服务和真实 SDK 验证，不代表已使用上述每家厂商的付费账号逐个实测。DashScope 原生协议、Anthropic Messages、Responses 和讯飞 WebSocket 的完整请求与响应结构仍需各自的协议适配，不能仅靠重命名用量字段接入。

## 统一字段

| 平台字段 | 接受的供应商字段 |
| --- | --- |
| 输入 `input_tokens` | `usage.prompt_tokens`、兼容响应中的 `usage.input_tokens` |
| 输出 `output_tokens` | `usage.completion_tokens`、兼容响应中的 `usage.output_tokens` |
| 缓存命中 `cache_tokens` | `prompt_tokens_details.cached_tokens`、`input_tokens_details.cached_tokens`、DeepSeek 的 `prompt_cache_hit_tokens`、Kimi 的顶层 `cached_tokens` |
| 推理 `reasoning_tokens` | `completion_tokens_details.reasoning_tokens`、`output_tokens_details.reasoning_tokens` |

缓存命中已经包含在输入 Token 内，推理明细不再叠加到输出 Token。Kimi 的 `cache_write_tokens` 不能当作缓存命中数。缓存写入、缓存存储、联网工具等额外项目不属于本轮新增计价项，不能据此声称已完整复现供应商账单。

只有 JSON 非负整数是有效计数。`false`、字符串、浮点数、负数和 `null` 均不能转成真实的零。多个同义字段同时出现时必须一致；缓存命中不能超过输入数；同时报告 DeepSeek 缓存命中与未命中时，其和必须等于输入数。报告了完整输入、输出和合计时，三者必须一致。

字段缺失或冲突保留未知；不根据 `total_tokens` 差值、可见回答长度或字节数补齐缺失的输入、输出。重排响应同时携带 `usage` 和 `meta.tokens` 时，两个位置提供的同义计数也必须一致。

## 流式对话

请求继续使用 `stream: true` 和 `stream_options.include_usage: true`。兼容接口需要支持这项请求参数，平台不在失败后自动重试生成。

用量可附在带 `finish_reason` 的结束块上，也可放在独立的统计块（`choices: []`）中。读取这些原始统计后再交给 SDK 转换回答文字，防止 SDK 将缺失字段合成为零或强制转换非法类型。

中途逐块累计统计不作为最终结算依据。重复的末尾统计只使用最后一份，向执行器发送一次用量快照；不逐块累加。后续末尾统计无效时，也不能退回使用前一份有效统计。流中断且只有中途用量时，实际用量保留未知。已收到明确的末尾计量后发生传输异常时，保留供应商已确认的用量事实。

## 向量与重排

向量接口按每个实际 HTTP 批次汇总供应商报告的输入量，输出计数为零。任何批次缺失输入计量时，整次输入量保持未知；不能只记录最后一批，或者将未知批次按零相加。批次间缓存明细不完整时，不把部分缓存统计当作完整折扣依据。

重排的输入、输出、缓存和推理通过同一规则归一。检索单元继续读取供应商的 `meta.billed_units.search_units`，独立于 Token 计数；缺少 Token 时，已确认的检索单元仍是有效事实。

## 自定义接口

| 返回情况 | 处理方式 |
| --- | --- |
| 对话协议与标准 `usage` 均兼容 | 直接使用实际计量 |
| 协议兼容，使用上表已识别的字段别名 | 归一后使用实际计量 |
| 只返回回答，没有有效计量 | 回答可保留，计量标为未知，沿用既有待核对规则 |
| 请求或响应结构完全不同 | 需要专用协议适配 |

自定义接口是否可计量，由协议和返回字段决定，与连接名称、厂商标签或域名是否官方无关。

## 智谱账户额度错误

智谱正式 API `open.bigmodel.cn` 返回 HTTP 429 且错误码为 `1113` 时，按[官方错误码定义](https://docs.bigmodel.cn/cn/api/api-code)展示服务商账户余额或资源包不足，保持明确拒绝和零消费处理，不作为重排任务的瞬态限流重试。分类只读取官方域名与固定错误码，不透传厂商原始消息。其他域名或冲突、未知错误码仍保留通用错误分类。

## 官方文档

- [DeepSeek 对话接口](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)
- [阿里百炼流式输出](https://help.aliyun.com/zh/model-studio/stream)
- [火山方舟 Chat API](https://docs.volcengine.com/docs/ark/chat-api?lang=zh)
- [硅基流动对话接口](https://docs.siliconflow.cn/docs/api/chat-completions-post)
- [智谱对话接口](https://docs.bigmodel.cn/api-reference/模型-api/对话补全)
- [Kimi 对话接口](https://platform.kimi.com/docs/api/chat)
- [百度千帆文本生成](https://cloud.baidu.com/doc/qianfan-api/s/3m7of64lb)
- [腾讯混元 OpenAI 兼容接口](https://cloud.tencent.com/document/product/1729/111007)
- [MiniMax 对话接口](https://platform.minimax.cn/docs/api-reference/text-chat-openai)
- [阶跃星辰 Chat Completions](https://platform.stepfun.com/docs/zh/api-reference/chat/chat-completion-create)
- [讯飞星火 HTTP 接口](https://www.xfyun.cn/doc/spark/HTTP调用文档.html)
