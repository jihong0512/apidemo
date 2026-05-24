# demo_10 — 智能断言与失败分析

AI 驱动的失败分析：双路径架构（LLM 精准分析 + 规则兜底），三层分类（环境/数据/代码Bug）。

## 输入文件
- `shared_data/step_09_results.json` — 执行结果（来自 demo_09）

## 必需 pip 包
无（标准库即可运行）

## 可选 pip 包
```bash
pip install deepdiff      # Schema 变更检测（主路径）
pip install openai        # LLM 精准分析
```

## 运行
```bash
cd demo_10_失败分析
python analyze_results.py
```

## 预期输出
- `shared_data/step_10_analysis.json` — 失败分析结果（含 root_cause + fix_suggestions + 类别分布）
- 控制台打印: "N 条需关注 (环境问题 2 条, 数据问题 1 条...)"
