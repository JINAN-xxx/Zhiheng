# 知识库固定使用 Milvus 实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 按以下步骤实施并验证。

**目标：** 根据用户确定的范围，新知识库默认且只允许 Milvus，页面不再提供其他存储类型。

**架构：** 创建请求模型限制 `kb_type`，类型接口只公开 Milvus 并单独返回图谱可用性；表单展示固定文本、统一生成 Milvus 请求。原有知识库按元数据继续读取。本机现有 1 个 Milvus 库、2 个文档，无需数据迁移。

**技术栈：** Vue 3、Ant Design Vue、FastAPI、Pydantic、Milvus。

## 步骤

- [ ] 后端：先在 `ruoyi-fastapi-backend/tests/plugins/ai/test_knowledge_runtime.py` 增加创建默认类型与类型能力接口测试，验证旧实现失败。

```python
assert DatabaseCreate(database_name='资料', embed_model_id=MODEL_ID).kb_type == 'milvus'
with pytest.raises(ValidationError):
    DatabaseCreate(database_name='资料', embed_model_id=MODEL_ID, kb_type='chroma')
assert set((await runtime.types())['kb_types']) == {'milvus'}
```

- [ ] 后端：`plugins/ai/knowledge/models.py` 改为 `kb_type: Literal['milvus'] = 'milvus'`；移除由 LightRAG 类型隐式开启图谱的创建逻辑。`runtime.py` 返回 `{'kb_types': {'milvus': capabilities['milvus']}, 'graph_available': capabilities['lightrag']['available']}`。底层历史存储读取保留。
- [ ] 前端：调整 `tests/plugins/knowledgeTransforms.test.js`，验证缺少类型和旧表单字段均生成 Milvus，图谱仍需模型；Milvus 不可用时不选择其他类型。
- [ ] 前端：`plugins/ai/utils/knowledge.js` 固定创建 payload 的类型为 Milvus；`views/knowledge/index.vue` 用固定 Milvus 文本替代类型选择，读取独立图谱能力，服务不可用时明确提示并禁止创建；`components/KnowledgeOverview.vue` 移除存储类型筛选。
- [ ] 浏览器：更新 `tests/e2e/knowledge_check.py` 的创建能力 fixture 与断言，验证无类型下拉框、POST 为 Milvus、Milvus 不可用时禁止创建。只执行桌面测试。
- [ ] 验证后端知识库相关测试、前端 34 组插件测试、Docker 模式构建，执行独立代码审查。

```bash
cd ruoyi-fastapi-backend
/home/ja/project/RuoYi-Vue3-FastAPI/.venv/bin/python -m pytest tests/plugins/ai/test_knowledge_runtime.py tests/plugins/ai/test_knowledge_controller.py tests/plugins/ai/test_knowledge_stores.py -q
cd ../ruoyi-fastapi-frontend
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker -- --outDir .milvus-only-dist
```

- [ ] 发布：在现有后端镜像上复制经过验证的两个 Python 文件，前端部署新产物；Compose 包装最新 `20260916-captcha-http/manage.sh`，保持 Nginx、FRP 及端口配置。
- [ ] 发布后检查 OpenAPI 的默认值/枚举、后端健康、真实 Milvus 连接、现有知识库数量、桌面知识库创建交互和公网验证码回归。记录新运维入口及回退方式。
