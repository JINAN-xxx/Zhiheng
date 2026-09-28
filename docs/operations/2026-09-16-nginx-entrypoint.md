# Nginx 统一外部入口

后续已修复公网 HTTP 验证码兼容，当前运维组合以 [验证码修复记录](2026-09-16-captcha-http.md) 为准；本记录中的 Nginx 配置和端口限制继续生效。

按用户要求，网页与 API 统一经过 `公网 8060 → FRP → Nginx 12580 → Docker 内部后端 9099`。

| 用途 | 对外路径 |
| --- | --- |
| 网页 | `http://115.190.121.37:8060/` |
| 页面业务接口与 API 文档 | `/docker-api/` |
| OpenAI 兼容接口、智能体开放接口 | `/v1/` |
| 原客户端兼容路径 | `/docker-api/v1/` |

开放 API Base URL 为 `http://115.190.121.37:8060/v1`。页面内既有 API 示例使用的 `/docker-api/v1` 仍经过同一 Nginx，继续可用。鉴权由后端执行。

## 配置

- 提交 `60735ce6` 更新 PostgreSQL 版 Nginx 配置与主 Compose。
- `/docker-api/` 转发时去掉前缀，`/v1/` 保留完整 URI。`/v1` 本身也转到后端，不再落入前端 SPA。
- 保留外部 Host 端口；Nginx 自身使用相对跳转，后端跳转保留 `8060`。
- API 使用 HTTP/1.1，关闭代理缓冲及缓存，读写超时为 300 秒，支持聊天与工作的流式响应。
- 后端宿主机端口仅为 `127.0.0.1:19099`，用于本机调试；Docker 内部仍使用 `zhiheng-backend-pg:9099`。

配置语义参考 [Nginx 代理模块](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)。

## 发布与验证

发布目录：`data/agent-releases/20260916-nginx-entrypoint/`。使用该目录 `manage.sh` 管理当前配置组合，Nginx 配置挂载到该目录内 `nginx.conf`，前端静态文件沿用 FRP HTTP 兼容版本。Compose override 用 `!override` 替换后端端口列表，要求 Compose 2.24.4+；当前为 2.36.2。

发布前确认没有进行中的聊天或工作，核对镜像、环境变量与持久化挂载一致后，仅重建两个应用容器。

验证结果：

- Nginx 语法、既有静态性能与部署契约检查通过。
- 隔离代理探针验证路径、查询参数、Authorization、POST 请求体和 Host 端口传递；两个前缀的 SSE 首帧约 2ms 到达，后续延迟帧正常返回。
- 本机和公网首页、OpenAPI 均返回 200。`/v1/models` 与兼容路径在无 API Key 时均返回 401 JSON，错误请求不再返回前端 HTML。
- 公网带无效 Key 的 `/v1/chat/completions` 返回 401 JSON，没有调用模型。
- `/docker-api` 的跳转为相对路径；后端规范化跳转保留公网端口 8060。
- 局域网 IP 的 19099 无法直连，本机 19099 调试入口仍正常，后端 healthy、FRP active。

回退 Nginx 时使用上一版 `20260916-frp-platform/manage.sh`；需要同时恢复旧端口绑定时重建两个应用容器。无数据库迁移。
