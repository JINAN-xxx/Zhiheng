# 第三方代码与许可

本项目根目录 LICENSE 为 MIT License，保留原作者版权声明。部分前端组件基于原 RuoYi / 智衡界面调整，文件中的来源说明继续保留。

本项目包含或使用以下单独声明许可的资源：

| 资源 | 仓库内许可或上游 |
| --- | --- |
| 智能体相关复用代码 | backend/src/zhiheng/modules/agents/LICENSE |
| LightRAG 适配所使用的资源 | backend/src/zhiheng/modules/knowledge/adapters/lightrag/LICENSE |
| 离线分词资源 | backend/src/zhiheng/modules/knowledge/adapters/lightrag/TIKTOKEN_LICENSE |
| Python 依赖 | backend/requirements-core.in 与 requirements-core.lock |
| 前端依赖 | frontend/package.json 与 package-lock.json |
| 部署镜像 | deploy/core/compose.yaml 所固定的上游镜像 |
| MinerU | 独立部署服务或官方云端 API，遵循上游项目许可及服务条款 |
| OpenSandbox SDK、管理服务及 execd/egress | [上游项目与许可](https://github.com/alibaba/OpenSandbox)，版本见部署配置和 deploy/sandbox/manager/requirements.txt |
| 办公执行镜像 | deploy/sandbox/runtime/Dockerfile 与 requirements.txt；LibreOffice、Noto 字体及 Python 包保留各自许可 |

依赖清单和本说明不将第三方软件重新许可为 MIT。分发包含第三方组件的镜像、代码或模型时，按实际版本保留对应许可与通知。官方云端 Key、模型供应商凭据、用户文件和部署配置不属于源码发布内容。
