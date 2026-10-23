# 图谱分词资源

提供 tiktoken 的 o200k_base 词表，构建时从本地复制并校验 SHA-256，运行请求不下载词表。

- 来源：https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken
- 缓存文件名：fb374d419588a4632f3f557e76b4b70aebbca790
- SHA-256：446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d
- 许可：同目录 LICENSE。

升级词表时同步更新 graph_tokenizer.py 的来源、缓存键和校验值，再重新验证构建。
