# demo_11 — 端到端实战

一键串联 demo_02~demo_10 全链路，从 Swagger 文档到失败分析报告。

## 输入文件
- `shared_data/sample_swagger.json` — 示例 Swagger 文档

## 必需 pip 包
```bash
pip install networkx     # 知识图谱
```

## 推荐 pip 包
```bash
pip install faker rank-bm25     # 数据工厂 + RAG
```

## 可选 pip 包
```bash
pip install chromadb neo4j openai dashscope langgraph deepdiff PyPDF2 python-docx PyYAML
```

## 运行
```bash
cd demo_11_端到端

# 跑全部 10 步
python run_pipeline.py

# 从第4步开始（跳过文档解析+知识图谱）
python run_pipeline.py --from-step 4

# 只跑 2-6 步（文档解析→用例生成）
python run_pipeline.py --from-step 2 --to-step 6

# 详细输出
python run_pipeline.py --verbose

# 单步失败不停止
python run_pipeline.py --continue-on-error
```

## 预期输出
- `shared_data/step_02_interfaces.json` — 解析的接口列表
- `shared_data/step_03_knowledge_graph.json` — 知识图谱
- `shared_data/step_04_dependencies.json` — 依赖分析
- `shared_data/step_05_test_data.json` — 测试数据
- `shared_data/step_06_test_cases.json` — 测试用例
- `shared_data/step_07_rag_index.json` — RAG 索引
- `shared_data/step_08_agent_cases.json` — Agent 补充用例
- `shared_data/step_09_results.json` — 执行结果
- `shared_data/step_10_analysis.json` — 失败分析报告
- 控制台打印: 10 步耗时汇总表 + 全部通过/有步骤失败
