# 智能对话图片与文档附件

代码提交 `dc46997d`，已发布到公网 `http://115.190.121.37:8060/`。当前运维入口为 `data/agent-releases/20260916-chat-attachments/manage.sh`。

## 行为

- 输入框左下角使用统一附件按钮，支持图片及 PDF、DOCX、TXT、Markdown、XLSX、CSV；显示文件名、大小、上传进度、失败重试和移除操作。
- 文档最多 5 个、每个 20 MiB；图片最多 10 张，使用用户图片大小偏好。文档单个提取文本上限 4 万字符，每次上下文合计 6 万字符。
- 可以只选择文件发送，默认消息为“请分析上传的文件。”。上传未完成或失败时禁止发送；发送请求失败保留草稿和文件；切换会话取消上传且不弹取消错误。
- 使用现有受保护上传和下载接口；后端按所有者、有效状态和路径边界读取文件。图片转换为模型多模态数据块，文档内容作为参考文本；历史保存文件元数据及文档快照，公开响应不返回解析正文。
- 历史图片读取时重新校验权限；历史上下文超限或不可用时有明确占位说明。图片预览识别实际文件头，兼容下载接口的 `application/octet-stream`。

当前启用的聊天模型只有 `deepseek-v4-flash`，平台配置 `vision=false`。文档可供该模型读取；图片理解需要另行配置支持视觉的模型，本次未更改任何模型能力标记。

文字型 PDF 支持提取，扫描件、加密或损坏文档会返回明确错误。PDF 的页面内容、嵌套 Form 与字体映射共享 32 MiB 解码预算，单流/单页上限 8 MiB，并有遍历深度和数量限制。该预算不是进程内存硬上限，底层 pypdf 的单流解码上限仍为 75 MB。Office 文档有压缩包展开量限制。未引入 OCR。

## 发布

- 后端镜像：`ruoyi-backend-pg:chat-attachments-20260916`，基于上一版 Milvus 镜像，只覆盖本次后端源文件；构建输入保存在发布目录 `backend/`。
- PostgreSQL 主依赖清单加入固定版本 `pypdf==6.18.0`、`python-docx==1.2.0`。当前镜像已包含这两个版本，另有 Pillow 和 openpyxl。
- 前端静态目录为发布目录的 `frontend/`；Nginx 挂载同目录 `nginx.conf`。
- Nginx 上传请求上限为 `101m`，覆盖后端通用 100 MiB 文件上限及 multipart 开销。补充通用上传白名单中的 WebP、MD、CSV。
- 新增 `data/platform-files:/app/vf_admin` 持久挂载，宿主目录权限为 0750。发布时停止旧后端后复制原目录内容，再重建两个应用容器；复制前物理文件数为 0，本次没有恢复既往缺失文件。
- 保留 FRP 8060、本机 Nginx 12580、后端仅绑定 `127.0.0.1:19099`，保留知识库及技能持久目录。

数据库通过插件迁移执行器应用 `migrations/postgresql/010_chat_attachments.sql`，为 `ai_chat_message` 新增默认空数组的 `attachments` JSONB 列。旧 9 项迁移按已成功记录跳过，新迁移记录为 success。一次性迁移使用的受限权限环境文件已删除，执行脚本保留在发布目录 `migrate.py`。

## 验证

- 最终后端相关测试 241 通过；前端插件测试 39 组通过；代码规格及质量审查通过。
- Docker 模式构建通过，5 个 Monaco worker 齐全。最终 Python 3.10 镜像内真实 PDF、DOCX、XLSX 解析验证通过。
- 真实 SQLite 元数据和受保护上传测试验证了文件落盘、所有者读取及越权拒绝；真实 ModelService 配合隔离模型引擎，验证图片及文档内容进入模型请求、历史上下文及响应过滤。
- 桌面浏览器在本机预览和公网 8060 均通过 4 组交互检查：多选/六种格式/大于 1 MiB 文件、混合和纯附件发送、失败重试、数量大小边界、切换会话取消、历史预览下载与视觉能力提示。这部分业务 API 使用隔离 fixture，未调用真实上游模型。
- 真实公网及本机大于 1 MiB 的未登录上传请求均到达后端登录校验，返回 code 401，不再被 Nginx 以 413 拦截；没有创建未授权文件。
- 公网验证码真实接口回归通过，验证码显示、刷新及故意无效验证码的加密登录校验正常，页面脚本错误为零。
- 线上 OpenAPI 确认新增 attachments 字段、最多 5 项；Nginx 语法正常、后端 healthy、FRP active。持久目录的宿主/容器内容一致性探针通过并已清理。
- 公网和本机首页一致，SHA-256 为 `85e964fdfec2c49592e369cd2af75e769e61a0a1c1898868b04013526c82f780`。

复测桌面上传交互（fixture）及真实验证码：

```bash
cd ruoyi-fastapi-frontend
PLATFORM_SHELL_BASE_URL=http://115.190.121.37:8060 ../.venv/bin/python tests/e2e/chat_attachments_check.py
CAPTCHA_CHECK_URL=http://115.190.121.37:8060 ../.venv/bin/python tests/e2e/captcha_transport_check.py
```

## 回退

回退应用镜像和静态文件时沿用当前持久挂载，避免新上传文件不可见。保留新增 JSONB 列及迁移记录，旧版本会忽略该列；不要删除附件数据。

已准备只覆盖旧后端镜像与前端静态目录的 `rollback.override.yml`。新的 Nginx 上传限制及文件持久目录继续保留；回退后恢复旧版的功能范围。

```bash
data/agent-releases/20260916-chat-attachments/manage.sh \
  -f data/agent-releases/20260916-chat-attachments/rollback.override.yml \
  up -d --no-deps --no-build ruoyi-backend-pg ruoyi-frontend
```
