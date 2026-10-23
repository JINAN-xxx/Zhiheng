# 前端渐进迁移与质量检查

使用 Node.js 24 和 npm 11。首次安装及 CI 均执行 `npm ci`；`package-lock.json` 纳入版本管理。更新依赖时使用 `npm install` 并同时审查 `package.json` 和锁文件，不使用 `--legacy-peer-deps` 绕过兼容性检查。

## 新增与迁移范围

| 范围                | 约束                                    |
| ------------------- | --------------------------------------- |
| `src/app/**`        | 应用装配、路由和初始化，使用 TypeScript |
| `src/shared/**`     | 跨域共享能力，使用 TypeScript           |
| `src/modules/**`    | 按业务域迁移的模块，使用 TypeScript     |
| `src/vite-env.d.ts` | Vite 环境变量声明，接受 lint 和类型检查 |

三个新目录按需建立，阶段 A 不添加占位运行时代码。Vue 单文件组件的脚本必须声明 `lang="ts"`。新代码显式导入 Vue、Vue Router 和 Pinia API，避免依赖旧版自动导入约定。迁移整个业务域时同步移动代码、补齐类型，并运行下列检查。

```sh
npm run lint:core
npm run format:check
npm run typecheck
npm run test:plugin
npm run build:prod
```

`lint:core` 对上述范围执行 ESLint 类型感知规则和 Vue 推荐规则，禁止显式 `any`；同时检查 ESLint 配置自身。`format:check` 仅检查新范围、环境声明和本次工程配置、清单。命令允许尚未创建的新目录，不代表旧站已通过 lint 或类型检查。

`typecheck` 使用 `vue-tsc --noEmit`，启用 `strict`、`strictTemplates`、索引访问检查和可选属性精确类型。`skipLibCheck` 仅跳过依赖库声明文件的内部检查。迁移代码导入旧 JS 时，应先迁移依赖或在边界添加准确的局部类型声明，不能用通配模块声明或全局 `any` 规避检查。

## 旧代码兼容清单

| 兼容范围                                                                              | 当前验证与退出条件                                                              |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `src/main.js`、`src/App.vue`、`src/permission.js`、`src/settings.js`                  | 保留现有入口，迁入 `src/app` 时接受新门禁                                       |
| `src/api/**`、`src/utils/**`、`src/design/**`、`src/directive/**`、`src/plugins/**`   | 保留 JS 和既有自动导入；迁入 `src/shared` 或对应业务域时补齐类型                |
| `src/components/**`、`src/layout/**`、`src/router/**`、`src/store/**`、`src/views/**` | 保留现有 Vue/JS；按域迁入 `src/app`、`src/shared` 或 `src/modules` 后接受新门禁 |
| `plugins/**`                                                                          | 保持既有插件机制，迁移具体插件时明确其类型边界                                  |
| `src/assets/**`、`public/**`、`vite/**`、`vite.config.js`、`index.html`               | 保留现有资源和构建约定，通过生产构建验证                                        |
| `tests/**`                                                                            | 保留插件契约测试及 Python Playwright 检查，通过现有测试入口验证                 |

兼容范围不进行全站自动格式化，不把旧 JS 加入 strict 工程，也不为旧 Vue 添加宽泛的 `*.vue` 模块声明。旧代码仍由插件契约测试、生产构建和 mock 浏览器验收覆盖。迁移完成后再从清单移除相应范围。

## CI 与本地生产验收

`.github/workflows/playwright.yml` 对 `master`、`release/**` 和 `refactor/**` 的推送及目标为这些分支的 PR 生效，执行锁定安装、质量检查、插件测试、浏览器辅助测试和生产构建，最后对 `vite preview` 运行既有 mock 浏览器检查。

浏览器夹具现有 API 前缀为 `/dev-api` 和 `/docker-api`。CI 在生产构建时设置 `VITE_APP_BASE_API=/dev-api` 以复用夹具；Vite 仍使用 production 模式。本地如需保留现有 `dist`，使用临时输出目录：

```sh
VITE_APP_BASE_API=/dev-api npm run build:prod -- --outDir /tmp/zhiheng-frontend-quality-build
npm run preview -- --outDir /tmp/zhiheng-frontend-quality-build --host 127.0.0.1 --port 15174 --strictPort
```

另一个终端设置 `PLATFORM_SHELL_BASE_URL=http://127.0.0.1:15174` 后运行现有 Python 浏览器检查；聊天工作模式检查使用 `CHAT_WORK_CHECK_BASE_URL` 设置相同地址。
