# 当前版本一键启动实测

执行目录：`/home/ja/project/zhiheng`。

代码分支：`fix/platform-integration-review`，包含当前未提交的审查修复与命名统一改动。

## 执行结果

按用户要求停止旧平台，随后在当前目录执行：

```bash
./deploy.sh init
```

命令退出码为 0。完成后端镜像构建、PostgreSQL 初始化、LiteLLM 运行密钥初始化、内置插件安装以及完整服务启动。

访问入口：`http://127.0.0.1:12580/`。

| 服务 | 验证结果 |
| --- | --- |
| `zhiheng-frontend` | running / healthy |
| `zhiheng-backend-pg` | running / healthy |
| `zhiheng-redis` | running / healthy |
| `litellm-db` | running / healthy |
| `litellm-redis` | running / healthy |
| `litellm-proxy` | running / healthy |
| `milvus` | running / healthy |
| `platform-init` | 正常完成，exit 0 |

数据库中 `ai` 和 `agent_extension` 插件均为 installed / enabled。新环境配置 `.env` 权限为 `0600`，已被 Git 忽略。

## 实际访问验证

- 首页、后端文档、OpenAPI 和验证码接口均返回 HTTP 200。
- 使用初始管理员完成真实验证码与登录验证，用户信息及菜单接口返回成功。
- 浏览器访问实际部署的智能工作台，等待网络请求和加载状态结束后正常渲染，无 HTTP 错误或未捕获页面异常。
- 验证会话已退出，临时登录令牌文件已删除。

## 旧服务及数据

停止并保留了 `ruoyi-frontend`、`ruoyi-backend-pg`、`ruoyi-redis`、`ruoyi-milvus`、旧 `litellm-proxy`、旧 `litellm-redis` 和旧 `litellm-db` 共 7 个容器，没有删除旧容器或数据卷。

旧容器配置清单保存在 `/home/ja/project/zhiheng-deployment-backups/20260928-165127/`，其中完整 inspect 文件含配置，权限为 `0600`。

本次是独立新环境启动验证：使用 `zhiheng` Compose 项目、新建的数据卷和当前目录的数据文件夹。旧业务数据没有迁入，也没有被覆盖；新页面是初始化状态，模型和业务配置需要另行接入或迁移。

## 验证边界与记录

镜像构建复用了本机已有的 Python 和依赖缓存；本次没有验证无缓存网络下载，也没有接入上游模型进行实际推理调用。

启动日志：`/tmp/zhiheng-oneclick-20260928-165127.log`。

HTTP、浏览器与服务状态记录：`/tmp/zhiheng-oneclick-validation/`。

当前新服务保持运行。查看状态可执行 `./deploy.sh status`。
