# 原平台源代码归档

`backend/` 和 `frontend/` 保存原平台实现及原有未提交优化，作为新平台提取业务能力和核对行为的参考，不进入新平台的打包、运行或 CI 检查范围。

归档使用目录移动，原有 55 个修改文件内容逐项校验保持一致，仍为未提交状态。仓库外原始备份与 SHA-256 清单保存在 `/home/ja/project/_local_backups/RuoYi-Vue3-FastAPI/20261023-110616-before-directory-migration/`。

正式后端位于根目录 `backend/src/zhiheng/`，正式前端位于根目录 `frontend/src/`。复用功能时补齐新模块边界和测试；新代码禁止导入这里的旧运行时。运行中的旧实例不因归档自动升级或重启。
