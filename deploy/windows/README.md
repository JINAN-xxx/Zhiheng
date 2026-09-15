# 平台局域网访问

当前平台地址：`http://172.29.106.4:12580/`。Windows/WSL 网卡使用 `172.29.106.4/23`，覆盖 `172.29.106.0/24` 与 `172.29.107.0/24`，两个网段的客户端使用同一平台地址。

Nginx 没有来源 IP 限制，Docker 已在所有网卡监听 `12580`。单个 server 中的 `server_name localhost` 不会限制客户端必须来自本机；未匹配 Host 的请求由默认 server 处理，参见 [Nginx 请求处理说明](https://nginx.org/en/docs/http/request_processing.html)。

本机 WSL 使用 mirrored 网络并启用防火墙。局域网入站需要同时满足 Windows 与 Hyper-V 的有效策略，参见 [Microsoft WSL 网络说明](https://learn.microsoft.com/en-us/windows/wsl/networking#mirrored-mode-networking)。

## 放行

在 Windows **管理员 PowerShell** 中执行本目录的 `enable-platform-lan.ps1`。脚本新增或更新以下两条专用规则：

| 规则 | 目标 | 协议 / 端口 | 允许的来源 |
| --- | --- | --- | --- |
| `Zhiheng-Platform-LAN-12580` | Windows 防火墙 | TCP / 12580 | `172.29.106.0/24`、`172.29.107.0/24` |
| `Zhiheng-Platform-WSL-LAN-12580` | WSL Hyper-V 防火墙 | TCP / 12580 | 同上 |

脚本不会关闭防火墙，也不修改默认入站策略。可以重复执行。撤销时，以管理员身份为同一脚本传入 `-Remove`，仅删除这两条命名规则。

## 验证

分别从两个网段的客户端运行：

```powershell
Test-NetConnection 172.29.106.4 -Port 12580
```

`TcpTestSucceeded` 应为 `True`，随后通过浏览器打开平台地址。服务端访问首页成功只能证明本机链路正常，不能替代客户端验证。如果规则生效后客户端仍无法连接，继续检查客户端代理、无线客户端隔离或上游网络策略。
