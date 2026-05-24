# demo_07 — RAG 知识库

三层混合检索：ChromaDB 向量检索 + BM25 关键词检索 + Reranker 重排序。

## 输入文件
- `shared_data/step_02_interfaces.json` — 接口列表
- `shared_data/step_06_test_cases.json` — 测试用例（用于构建文档索引）

## 必需 pip 包
```bash
pip install numpy rank-bm25
```

## 可选 pip 包
```bash
pip install chromadb       # 向量数据库（主路径）
pip install dashscope      # 通义千问 Embedding + Reranker
```

## 运行
```bash
cd demo_07_RAG知识库
python build_index.py
```

## 预期输出
- `shared_data/step_07_rag_index.json` — 文档索引（含文档块和元数据）
- 控制台打印: "N 条文档已索引"
