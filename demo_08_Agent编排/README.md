# demo_08 — Agent 编排系统

LangGraph StateGraph 编排三 Agent 协作：接口解析 → 依赖分析 → 用例生成。

## 输入文件
- `shared_data/step_02_interfaces.json` — 接口列表
- `shared_data/step_07_rag_index.json` — RAG 知识库索引

## 必需 pip 包
无（标准库即可运行，顺序调用降级路径）

## 可选 pip 包
```bash
pip install langgraph      # LangGraph StateGraph（主路径）
pip install openai         # LLM Agent 决策
```

## 运行
```bash
cd demo_08_Agent编排
python run_agent.py
```

## 预期输出
- `shared_data/step_08_agent_cases.json` — Agent 生成的补充用例 + 执行日志
- 控制台打印: "N 条补充用例"
