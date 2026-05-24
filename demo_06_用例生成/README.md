# demo_06 — 用例生成引擎

8段式 Prompt 驱动 LLM 生成 pytest + HttpRunner 测试用例，LLM 不可用时降级到模板引擎。

## 输入文件
- `shared_data/step_02_interfaces.json` — 接口列表
- `shared_data/step_05_test_data.json` — 测试数据（来自 demo_05）

## 必需 pip 包
无（标准库即可运行）

## 可选 pip 包
```bash
pip install openai    # LLM 路径（DeepSeek 兼容）
```

## 运行
```bash
cd demo_06_用例生成
python generate_cases.py
```

## 预期输出
- `shared_data/step_06_test_cases.json` — pytest 测试函数 + HttpRunner YAML + conftest
- 控制台打印: "N 条 pytest 用例"
