# 模型快速接入简化验证记录

## 范围

- 分支：`feat-litellm-deployment`
- 功能提交：`276cc3c`、`9f7f20a`、`f4fc7a6`、`3c480b4`、`3cc81f7`、`99824e1`
- 验证日期：2026-08-19
- 正式访问地址：`http://127.0.0.1:12580/`

本次只替换前端容器。后端、PostgreSQL、Redis 和 LiteLLM 未重建，浏览器流程使用与 Controller VO 对齐的 Mock API，不访问真实 Provider，也不记录任何 Key 或 Credential。

## 自动化验证

```bash
cd frontend
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
```

结果：

- 插件测试 6 组全部通过。
- Vite 构建退出 0，转换 8389 modules。
- 仅保留第三方 VueUse PURE 注释告警，不影响产物。
- `git diff --check` 无输出。

TDD 红灯覆盖：快速资源命名/步骤导出缺失、表单 quick 属性缺失、旧四步标题、高级管理入口缺失、展示名称超过后端 100 字符约束，以及 Catalog 写操作被全局重复提交保护阻断。

## 浏览器验证

开发服务与正式容器分别执行：

```bash
conda run -n dj python /tmp/model_onboarding_simplification_check.py
MODEL_CENTER_BASE=http://127.0.0.1:12580 conda run -n dj python /tmp/model_onboarding_simplification_check.py
```

两次结果一致：

```text
credential_posts=1
deployment_posts=1
model_posts=1
binding_puts=1
publish_posts=2
delete_calls=0
390 viewport/document/drawer=390/390/390
320 viewport/document/drawer=320/320/320
consoleErrors=[]
pageErrors=[]
```

验证场景：

- 默认只显示“平台模型”，主动点击“高级管理”后才显示 Deployment、Credential、Runtime 标签，收起后恢复主视图。
- 新凭据流程只要求 Provider、动态凭据字段、上游模型和平台名称；Credential Name、Deployment Alias 自动生成。
- 已有兼容凭据可直接复用，不重复创建 Credential。
- Secret input 为 `password` 且初始值为空。
- 连接测试确认、Deployment 创建前对账、Catalog/Binding/Publish 顺序保持不变。
- Publish 第一次失败后保留 Credential、Deployment、Model 和 Binding；立即重试只再次发送 Publish，不重复创建前置资源，也不发送 DELETE 补偿。
- 三步标题依次为“连接信息”“测试模型”“确认接入”；Deployment 与 Catalog 高级参数默认折叠。
- 1440、390、320 三个视口无文档横向溢出，移动抽屉宽度不超过视口。

截图：

- `/tmp/model-center-quick-default-desktop-1440.png`
- `/tmp/model-onboarding-quick-desktop-1440.png`
- `/tmp/model-onboarding-quick-mobile-390.png`
- `/tmp/model-onboarding-quick-mobile-320.png`

## 正式容器

- 新镜像：`zhiheng-frontend:quick-onboarding`
- 新镜像 ID：`sha256:f69ce0e7b2cd488097042c893cb4d71a824b357cf9a389fff6222aff25386308`
- 运行标签：`zhiheng-frontend:latest`
- 回滚标签：`zhiheng-frontend:pre-quick-onboarding`
- 回滚镜像 ID：`sha256:35b1bc7669c643202a3c119fdad56b0937bbbc56494a22d3d9fc4191ede0dc74`
- 本地与容器 `index.html` SHA256：`201fb2496daa1dd61d2ff4e6cb82cf78bafcdb759eb58a0dafe308a25aa55c32`
- 页面标题：`智衡 AI`

运行状态检查确认 `zhiheng-frontend`、`zhiheng-backend-pg`、`zhiheng-redis`、`litellm-proxy`、`litellm-db` 和 `litellm-redis` 均为 running；带健康检查的服务均为 healthy。

## 残余边界

- 浏览器端到端验证未调用真实 Provider，避免产生费用或泄露凭据；真实 Provider 连通性仍由用户在接入步骤中主动确认后测试。
- 自动资源名使用每次打开抽屉时生成的稳定后缀；关闭后重新接入会生成新后缀，失败重试期间保持不变。
