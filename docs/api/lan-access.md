# 局域网访问与浏览器兼容

其他电脑通过服务器的局域网 IP 和网页端口访问，例如 `http://<服务器局域网 IP>:19080`。页面、API、流式响应和下载使用同一入口；浏览器中的 `localhost` 指访问设备本身。

HTTP 局域网地址与本机回环地址的浏览器权限不同。当前前端统一使用：

- `frontend/src/shared/requestId.ts`：优先使用浏览器 UUID 接口，不可用时由 `crypto.getRandomValues` 生成 UUID。重试沿用业务流程冻结的原请求编号。
- `frontend/src/shared/clipboard.ts`：优先使用现代剪贴板接口，不可用或被拒绝时使用选中文本复制，并恢复原来的焦点和选区。失败时保留手动复制提示。
- `frontend/src/shared/markdownClipboard.ts`：补充 Markdown 代码块在 HTTP 地址下的复制。
- ESLint 禁止业务组件直接调用上述受限浏览器接口，避免新功能只在本机地址下可用。

登录 Cookie、资源权限和服务端来源检查继续生效。浏览器密码保存能力仍由安全上下文和浏览器自身控制；平台不通过本地存储保存账号密码来替代它。

源码模式的入口由 `dev.sh`、`deploy/core/compose.source.yaml` 和本机可选的 `deploy/core/.source/compose.lan.yaml` 组成。入口监听地址、Windows/WSL 转发与防火墙共同决定其他设备能否连接。浏览器能力兼容性通过，不等于已经从另一台物理设备验证网络入口。

局域网回归使用显式的 `PLATFORM_BROWSER_URL`。测试剪贴板时，`PLATFORM_CLIPBOARD_READER_URL` 可指向同一测试服务器的回环或 HTTPS 页面，仅用于读取测试结果，不给被测 HTTP 页面注入剪贴板接口。浏览器测试中的 API 夹具与真实后端链路测试分别记录。
