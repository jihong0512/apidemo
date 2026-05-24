# demo_04 — 依赖分析

分析接口依赖关系：Kahn 拓扑排序 + 数据流推断 + DFS 环检测 + 32组业务分组。

## 输入文件
- `shared_data/step_02_interfaces.json` — 接口列表
- `shared_data/step_03_knowledge_graph.json` — 知识图谱（可选，优先从 Neo4j 获取）

## 必需 pip 包
无（标准库即可运行）

## 运行
```bash
cd demo_04_依赖分析
python analyze_deps.py
```

## 预期输出
- `shared_data/step_04_dependencies.json` — 执行顺序 + 依赖图 + 数据流链 + 业务分组
- 控制台打印: "执行顺序: [接口1, 接口2, ...]"
