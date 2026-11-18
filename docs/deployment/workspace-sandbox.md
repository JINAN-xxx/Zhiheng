# 工作空间执行环境

工作空间复用平台模型、权限、计费、任务运行和 MinIO 文件服务。Python 与 Shell 在独立的 OpenSandbox 环境执行，API / Worker 通过认证 HTTP 接入管理服务。

## 组成与版本

- `opensandbox==1.2.1`：平台内的官方 Python SDK。
- `opensandbox-server==1.2.1`：独立管理服务镜像 `zhiheng-sandbox-manager:1.2.1`，使用单独的 Python 环境。
- `opensandbox/execd:release-1.2.1`：提供执行与进程隔离程序；仅拉取此镜像还不具备办公 Python 环境。
- `opensandbox/egress:release-1.2.1`：官方出口策略服务，默认拒绝外连，只允许管理员配置的包索引域名及 PyPI 文件域名。
- `zhiheng-workspace-runtime:0.1.0`：办公执行镜像，包含表格、Word、PDF、图片依赖，LibreOffice 和中文字体。

镜像标签可通过 `CORE_SANDBOX_*_IMAGE` 指定。使用镜像仓库镜像站时，应填写实际拉取的完整镜像名。发布环境应固定受信任版本或镜像摘要。

## 启用

真实配置保存在被 Git 忽略的 `deploy/core/.env.core`。为沙盒使用独立随机 `CORE_SANDBOX_API_KEY`，不要复用数据库、模型或对象存储密码。需要启用的实例设置：

```dotenv
CORE_SANDBOX_URL=http://host.docker.internal:19192
CORE_SANDBOX_RUNTIME_IMAGE=zhiheng-workspace-runtime:0.1.0
CORE_SANDBOX_MANAGER_IMAGE=zhiheng-sandbox-manager:1.2.1
CORE_SANDBOX_EXECD_IMAGE=opensandbox/execd:release-1.2.1
CORE_SANDBOX_EGRESS_IMAGE=opensandbox/egress:release-1.2.1
```

从仓库根目录执行：

```bash
COMPOSE_PROFILES=workspace ./deploy.sh build sandbox-manager sandbox-runtime
COMPOSE_PROFILES=workspace ./deploy.sh up
```

源码模式先完成上述镜像构建，再运行 `COMPOSE_PROFILES=workspace ./dev.sh up`。`deploy.sh init` 为新实例生成独立沙盒 API key；已有配置需手动补齐，不覆盖旧密钥。管理服务是可选的 `workspace` profile；未启用时，核心平台配置仍可校验，普通对话、助手和工作空间普通问答不依赖沙盒。代码执行还需要单独授予 `platform:work:execute_code`。任务接口与恢复见[工作空间任务 Agent](../api/workspace-agent.md)。

管理服务采用 host 网络，只绑定 Docker 默认网桥的网关地址，默认端口 19192，不绑定局域网地址。API / Worker 通过 `host.docker.internal:host-gateway` 访问。官方 SDK 使用经认证的服务端代理访问执行端口；这些端口也只发布在 Docker 网桥地址上。外部不需要开放执行端口。

`CORE_SANDBOX_DNS_SERVERS` 用于出口侧车。WSL 的 DNS 隧道地址通常只供宿主进程使用，无法直接供默认 Docker 网桥容器访问。示例使用公共解析器 `223.5.5.5,1.1.1.1`；企业内网可填写从 Docker 网桥可访问的企业 DNS。

## 隔离与容量

管理服务属于可信控制面，只有它挂载 Docker socket。API、Worker 和任务容器均不挂载此 socket，也不把模型、数据库、MinIO 或管理服务凭据传入任务环境。

每轮任务创建一个临时容器。模型代码通过官方 `execd` strict isolated session 运行，使用独立 user namespace、PID / mount namespace 和私有网络 namespace，进程内 UID/GID 为 10001，实际有效能力集为 0。输入资料使用只读绑定；只有任务工作目录和临时目录可写。源码、文件解析器、环境准备器和监督器来自固定镜像；所有可信 Python 启动器均使用绝对路径与 `-I`，防止工作目录模块和环境变量劫持。

Docker 模式在 ext4 上不能使用 overlay2 `storage-opt` 写入配额。本部署对官方 Docker host-config 增加小范围保护钩子，使用内核实际限制的 tmpfs：工作目录默认 1 GiB，临时目录与私有快照目录各 128 MiB。isolated session 的私有 `/tmp` 位于受限的外层临时 tmpfs 中。并发、CPU、内存、PID 数、单步超时、输出长度、单文件 20 MiB、快照 64 文件 / 100 MiB 等约束同时生效。

官方 isolated session 需要 Linux user namespace 支持和 `bootstrap.execd.isolation=enable`。其外层 execd 容器按官方机制取得 `SYS_ADMIN` 与 mount 所需的 seccomp / AppArmor 配置；模型进程处于另一个受限 namespace，不保留这些权限。此机制是 Linux 进程隔离，不宣称硬件虚拟机级别的保护。缺少隔离能力时明确拒绝执行，不降级在宿主机运行。

## 文件与恢复

正式输入、成果和跨轮工作快照统一经过文件服务存入 MinIO。沙盒 tmpfs 只承担执行现场；依赖虚拟环境和 wheel 暂存是可重建缓存，不进入任务工作快照。工作目录允许执行映射，使按需安装的 OpenCV 等本地扩展可以加载；仍保留 `nosuid`、`nodev`、隔离用户、只读输入和目录大小限制。

`collect_outputs()` 与 `snapshot_files()` 会先销毁当前隔离进程 namespace，停止其后台子进程，再采集固定字节。之后仍可在同一个临时容器内创建新的隔离 session 继续修正文件。正式结束、取消或失败时才销毁整个容器。

成果格式检查、Word / Excel 转 PDF 和图片预览在新的非 root、无网络隔离 session 中执行。预览按固定字节返回，由平台保存至 MinIO 并执行同样的资源权限校验。最多转换前五个 Office 成果，PDF 预览最多前六页；结构检查与预览生成不代表业务数字或视觉排版已经自动证明正确。

对于同一输入路径，重复提供相同字节可安全复用；不同字节会被拒绝。采集拒绝符号链接、非普通文件、越界路径和超限文件，并对传输后的字节重新校验 SHA-256。

管理服务增加受同一 API key 保护的 `/platform/profile` 与 `/platform/sandboxes/{id}/residue`。前者报告实际保护配置；后者仅清理该沙盒编号所对应的官方出口侧车和带 server 所有权标记的 runtime volume。平台在主体容器消失后继续确认这些附属资源回收；不能确认时保留清理中状态，不把 404 当成全部进程已回收。

## 验证

基础测试不需要真实模型或管理服务：

```bash
cd backend
python -m pytest tests/unit/test_sandbox_adapter.py -q
```

真实执行测试使用独立的管理服务，设置 `ZHIHENG_TEST_SANDBOX_URL`、`ZHIHENG_TEST_SANDBOX_API_KEY` 和可选 `ZHIHENG_TEST_SANDBOX_IMAGE` 后执行：

```bash
python -m pytest tests/core/integration/test_workspace_sandbox.py -q
```

覆盖真实文件制作与 PDF 预览、快照恢复、只读原件、路径与符号链接、非 root / 无能力集 / 私有网络、实际 scratch 容量、超时、取消、依赖安装和工作目录模块遮蔽。

另有 `tests/core/integration/test_work_agent_execution.py` 通过公共工作空间 API、真实 ARQ、官方 LangChain/PostgreSQL checkpoint 和原生计量运行完整任务。Office 用例要求三个 `ZHIHENG_TEST_SANDBOX_*` 变量全部提供，验证 Excel/Word 制作、PDF 预览、续写与旧版本字节保持。该层模型端使用测试专用 OpenAI 兼容 HTTP 服务，不表示真实云端模型已验证。
