# 模型目录、容量声明与执行准备

ModelService显式组合connections、catalog、bindings、configuration和rates，调用方从public.py取得服务与契约，再直接访问实际职责。根服务不保留旧方法转发、动态属性或并行实现。

| 文件 | 职责 |
|---|---|
| connections.py | 连接管理、最小选项、凭据加密与不可变ConnectionRevision、引用保护 |
| catalog.py | 模型目录、创建/更新/停用/删除、账务元数据与完整有效模型清单 |
| bindings.py | 按固定顺序加锁和冻结ModelBinding、原版本供应商准备、清理专用retained |
| configuration.py | 保存前配置与权限检查、供应商校验决策、保存后权限/CAS/连接版本复核 |
| rates.py | 平台配置的货币RateCard版本及报价；独立于billing的积分价格 |
| projections.py | DTO、平台可用状态及Binding构造；不访问凭据或执行SQL |
| capacity.py / contracts.py | 有效预算、容量依据状态及统一ModelConfiguration验证 |
| application/models/configuration.py | 事务外供应商IO与保存事务的组合 |
| application/models/management.py、trials.py | 跨域引用保护和原生Run试用准入 |

例子：models.catalog.list、models.connections.options、models.bindings.resolve、models.bindings.provider、models.configuration.prepare/save_verified、models.rates.latest。

## 配置验证与保存

API创建直接使用ModelConfiguration，内部目录写入与合并后的部分更新也使用同一验证。名称/模型标识非空，文本不能含空字符，容量为严格整数；只有chat允许vision，聊天最大输出与安全余量不能占满窗口。校验在改变模型属性前完成；非法配置返回明确422，不在供应商IO后才发现本地错误。

供应商目录校验只能确认模型身份，不能证明窗口、输出或视觉能力。新建、改变连接/上游标识、重新启用会校验；仅修改名称、容量、视觉声明和依据使用本地验证，不解密凭据或读取供应商目录。停用连接下可修正本地资料，这不授权模型调用。

保存前读取配置与连接版本，供应商IO不持有业务SQL锁；保存时重新恢复当前权限、比较模型快照，并复核原连接版本。模型→连接锁序及绑定SHARE锁保留。retained只提供清理所需身份，不检查或授予运行权限；真实resolve仍要求模型和连接均可用。

## 容量与依据

context_window_tokens是按当前接入服务配置的聊天窗口，max_output_tokens是本平台允许的输出上限。模型卡原生能力、可扩展能力和托管部署限制可能不同；无法确认时保持未知，不根据名称、输出上限或目录可访问推导真实窗口。

capacity_note记录文档链接、版本或部署限制。聊天模型的capacity_status为unknown（窗口未知）、unverified（窗口有值但未提供依据）或documented（已记录依据）；documented不等同于自动认证。向量/重排为not_applicable，不能把单条文本或查询/文档对限制当成聊天窗口，也不展示无关的聊天输出设置。

默认输入预算继续使用模型窗口、平台预算、默认输出预留和安全余量的共同规则。声明未知时预算仍可来自平台；已知窗口与显式输出在真实调用前继续检查。capacity_note不进入ModelBinding，因此不会改变历史向量generation的绑定指纹。

0037_model_capacity_note只增加非空、默认空串的依据列，不重写历史Run、Binding、价格或账本。数值校准属于明确的管理操作，应保留读取来源、日期、前后值，并通过原权限与并发检查保存。

## 平台可用性与能力声明

availability只根据模型和连接当前状态给出model_disabled、model_deleted、connection_missing、connection_disabled或connection_deleted。它不发网络探测，也不声称供应商凭据、网络或余额正常。管理页共用这些原因；真实调用仍走原准入与供应商边界。

capabilities由模型类型及视觉开关声明产生；视觉应按实际接入服务能力配置，目录校验成功不自动启用视觉。凭据不通过模型目录回读，配置人员继续使用最小ConnectionOption。

货币RateCard是平台配置的计算口径，不等于供应商实际账单。该服务与积分价格各保留原版本和事务，结构整理不合并两种金额。
