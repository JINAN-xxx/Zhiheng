# HTTP 入口验证码兼容修复

后续已固定知识库存储为 Milvus，当前运维组合以 [知识库发布记录](2026-09-16-knowledge-milvus-only.md) 为准；本记录中的 HTTP 加密兼容继续生效。

当前运维入口为 `data/agent-releases/20260916-captcha-http/manage.sh`，组合上一版 Nginx 入口配置并更新前端静态文件。公网入口仍为 `http://115.190.121.37:8060/`，API 经 `/docker-api/` 转发。

## 原因与修复

公网 HTTP 页面没有 `crypto.subtle`，原前端传输加密模块在验证码请求发送前抛出“当前浏览器不支持 Web Crypto API”。直接访问验证码接口返回正常，因此单独检查接口状态无法发现此问题。

代码提交 `9b7ea8d6`：

- 加密能力可用时优先使用原生 Web Crypto；HTTP 环境按需加载固定版本 `node-forge@1.4.0`，实现既有协议需要的操作。
- 保持 RSA-OAEP SHA-256/MGF1-SHA256 与 AES-256-GCM 协议、12 字节 IV、AAD 和 128 位认证标签。随机数及 OAEP seed 均由浏览器 `getRandomValues` 提供；缺少安全随机数时拒绝执行。
- AES 密钥内容保存在私有 WeakMap，使用可经过 Axios 配置合并的非普通对象句柄。
- 验证码图片 MIME 修正为后端实际返回的 PNG。

Web Crypto 的安全上下文限制见 [MDN Crypto.subtle](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/subtle)；随机数能力见 [MDN Crypto.getRandomValues](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/getRandomValues)。兼容实现使用 [Forge 官方 API](https://github.com/digitalbazaar/forge)。

## 验证

- 34 组前端插件测试通过，其中兼容层 12 项覆盖 RSA/AES 原生互通、Unicode、偏移视图、AAD/密文/标签篡改拒绝、安全随机数缺失、密钥隔离与 Axios 合并。
- 独立代码审查未发现阻塞问题。Docker 模式构建通过，5 个 Monaco worker 产物齐全；构建仍提示既有大文件分块警告。
- 发布后使用 Chromium 直接访问公网 8060 和本机 12580，调用真实后端，无业务 API 模拟：验证码图片均为 160×60；首次加载、页面重载、手动换图、错误提交后的换图均通过；页面脚本错误为零。
- 公网检查 `isSecureContext=false`，本机检查为 `true`，覆盖兼容与原生两条路径。验证码 GET 和登录 POST 的请求、响应均确认保持加密。
- 使用不存在的测试用户名及故意错误的验证码，后端按预期拒绝并返回“验证码错误”；未使用真实账号登录。
- Nginx 语法通过，后端 healthy，FRP active。公网与本机首页内容一致，SHA-256 为 `df1122f0e214c11eb943fb9f171d1b725605efb561feccc72a850d8308f79bac`，公网 OpenAPI 返回 200。

重复检查命令（需安装 Playwright Chromium，目标启用传输加密）：

```bash
cd frontend
CAPTCHA_CHECK_URL=http://115.190.121.37:8060 ../.venv/bin/python tests/e2e/captcha_transport_check.py
```

## 发布与回退

发布目录内 `compose.override.yml` 仅替换前端静态挂载。沿用 `20260916-nginx-entrypoint/nginx.conf`，后端宿主机端口仍绑定 `127.0.0.1:19099`。本次仅重建前端容器，无数据库迁移。

```bash
data/agent-releases/20260916-captcha-http/manage.sh up -d --no-deps --no-build zhiheng-frontend
```

回退使用上一版入口，仅重建前端；回退会重新出现公网 HTTP 的验证码问题：

```bash
data/agent-releases/20260916-nginx-entrypoint/manage.sh up -d --no-deps --no-build zhiheng-frontend
```
