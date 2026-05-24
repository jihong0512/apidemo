# demo_03 — 知识图谱

从接口列表构建 API 知识图谱：APIInterface 节点 + DEPENDS_ON 关系。

## 输入文件
- `shared_data/step_02_interfaces.json` — 来自 demo_02

## 必需 pip 包
```bash
pip install networkx
```

## 可选 pip 包
```bash
pip install neo4j              # Neo4j 图数据库（主路径）
pip install langchain-community # LangChain GraphCypherQAChain（自然语言查询）
```

## 运行
```bash
cd demo_03_知识图谱
python build_graph.py
```

## 预期输出
- `shared_data/step_03_knowledge_graph.json` — 节点列表 + 边列表 + 依赖类型统计
- 控制台打印: "N 节点, M 边"
