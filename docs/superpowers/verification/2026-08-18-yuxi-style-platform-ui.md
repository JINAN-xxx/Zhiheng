# 智衡 AI 统一界面验证记录

验证日期：2026-08-18

验证分支：`feat/yuxi-style-platform-ui`

## 自动化验证

- `npm run test:plugin`：5 组插件测试通过。
- `NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker`：退出码 0，Vite 成功转换 8392 个模块并生成生产产物。
- `conda run -n dj pytest tests/plugins/sample_plugins/test_ai_plugin.py -k 'not structure_check_passes' -q`：4 passed、1 skipped、1 deselected。
- `git diff --check`：无输出。

构建仅出现既有第三方 `@vueuse/core` PURE 注释位置提示及大包体提示，没有 Vue、路由或样式编译错误。

## 浏览器验证

Python Playwright 使用与后端 VO 对齐的 Mock API，不访问真实用户、模型、凭据或 Key。验证视口为 1440x960、768x1024、390x844 和 320x720。

| 场景 | 1440 | 768 | 390 | 320 | 结果 |
| --- | --- | --- | --- | --- | --- |
| 登录页 | 通过 | 通过 | 通过 | 通过 | 品牌图像、表单与验证码关闭场景正常 |
| AI 对话 | 通过 | 通过 | 通过 | 通过 | 历史会话、消息区、移动会话抽屉和输入区无页面级溢出 |
| 模型中心 | 通过 | 通过 | 通过 | 通过 | 四个标签均可切换，无控制台或页面异常 |
| Developer API | 通过 | 通过 | 通过 | 通过 | 应用、Key、授权模型与移动布局正常 |
| 用户/监控/代码生成 | 通过 | 通过 | 通过 | 通过 | 三类管理页面无页面级溢出 |

所有上述页面均满足 `document.documentElement.scrollWidth <= innerWidth`，浏览器 `console error` 与 `page error` 均为 0。1440 视口额外验证了实际亮/暗主题切换。

登录后从 `/index` 进入时，动态路由使用 `/ai` 父路径和相对 `chat` 子路径；浏览器确认最终地址为 `/ai/chat`。无 AI 对话路由的权限场景由纯函数测试确认保留 `/index`。

移动会话抽屉在 390px 和 320px 下验证了搜索、切换历史会话和新建会话。独立 Mock SSE 场景验证了流式内容、思考过程、用量、完成状态、503 失败提示和挂起请求停止；预期的 503 浏览器网络记录单独白名单处理，其余 console/page error 为 0。

Developer API 创建 Key 场景确认：完整 Key 只在创建结果弹窗中展示；关闭弹窗后，完整 Key 不再存在于 DOM。
创建请求体中的 `warningRatio` 和 `hardLimitRatio` 分别为十进制字符串 `"0.8"`、`"1"`，并通过后端 `DeveloperKeyCreateModel` 校验。同应用 Key 刷新失败保留旧数据并标记可能过期；缺少 `key:list` 权限时不发送 Key 请求并显示无权限状态。

截图保存在 `/tmp`，未提交构建产物或浏览器脚本。主要截图包括：

- `/tmp/yuxi-login-desktop.png`
- `/tmp/yuxi-login-mobile-320.png`
- `/tmp/yuxi-chat-desktop.png`
- `/tmp/yuxi-chat-mobile-320.png`
- `/tmp/yuxi-application-desktop.png`
- `/tmp/yuxi-application-key-once.png`
- `/tmp/yuxi-admin-role-desktop.png`
- `/tmp/yuxi-admin-role-mobile-320.png`

## 视觉与安全边界

- 统一使用青绿色主色、白色/浅灰工作表面、8px 以内圆角和语义状态色；没有新增紫色渐变、装饰光球或嵌套卡片布局。
- 登录/注册页的青绿色渐变仅作为真实背景图片上的可读性遮罩，不是纯渐变主视觉。
- 本分支新增生产代码没有 `v-html` 或调试 `console` 输出；测试中的示例 Key 仅用于一次性 Key 转换契约。
- Developer API 的列表归一化显式丢弃意外返回的完整 `apiKey`；仅创建/轮换响应允许进入一次性展示状态。
- 图标命令使用 Element Plus 图标，并提供 `aria-label`、tooltip 或可见文字；状态同时提供文字，不只依赖颜色。

## 非阻断项

- 完整 `test_ai_plugin.py` 的 `structure_check_passes` 仍会因既有 `openai_compat_controller.py` 使用 `/v1` 前缀而失败；新增 Developer API 视图和菜单的独立结构检查通过。本次界面改造没有修改该兼容接口命名空间。
- 生产构建仍有既有大包体提示；本次未引入新的打包拆分策略。
