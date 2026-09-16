# 平台 FRP 公网入口

后续已统一 Nginx API 入口并收紧后端端口，当前运维组合以 [Nginx 入口记录](2026-09-16-nginx-entrypoint.md) 为准。FRP 公网地址与本地目标端口保持本记录配置。

已按用户要求使用 `/frp` 中的 FRP 客户端，并将公网端口设为 **8060**。

- 公网地址：`http://115.190.121.37:8060/`。
- FRP 服务端：`115.190.121.37:7000`。
- 客户端：`/frp/frpc`，版本 0.54.0。
- 配置：`/frp/frpc.toml`，代理名 `llm_demo`，TCP 转发至 `127.0.0.1:12580`。
- 原配置已在 `/frp` 内备份；配置与备份均限制为 0600，认证值不提交 Git。
- 旧公网端口 8050 已关闭。

## 服务管理

客户端由 `/etc/systemd/system/frpc-platform.service` 托管，以 `dev` 用户运行，并已启用。单位文件副本在 `deploy/frp/frpc-platform.service`。

```bash
systemctl status frpc-platform.service
systemctl restart frpc-platform.service
systemctl stop frpc-platform.service
journalctl -u frpc-platform.service -n 30 --no-pager
```

服务设置为启动失败自动重试，并在 WSL 系统启动时启动。宿主机、WSL 和平台 Docker 服务需要保持运行；本次不修改 Windows 防火墙规则。

## HTTP 浏览器兼容

公网 HTTP 入口的 Chromium 未提供 `crypto.randomUUID`，但支持 `crypto.getRandomValues`。检查复现了聊天无法发送、工作无法生成请求编号的问题。提交 `8387edf6` 将四处直接调用改用已有 `uuid` 依赖的 v4 生成器，在保留安全随机数的同时兼容 HTTP 来源。

回归测试在移除原生 randomUUID 后先复现失败，修复后通过。33 组前端插件测试和 Docker 模式构建通过；公网 8060 上的桌面聊天发送、归档恢复、延迟详情恢复以及工作失败重试验证通过。浏览器交互验证使用隔离业务 API 数据，不调用真实上游模型。

## 发布定位

兼容性前端产物位于 `data/agent-releases/20260916-frp-platform/frontend/`，运维入口为同目录 `manage.sh`。此次仅更新前端容器。

公网首页与本机首页字节一致，SHA-256 为 `73f4c362a566eec96f6f7ac3231103d0ac4c4c29f96674bba4089fd092b86f34`；公网 `/docker-api/openapi.json` 返回 200，后端保持健康。FRP 日志确认登录成功及代理启动成功，服务状态为 active/enabled。
