# demo_09 — 异步执行引擎

线程池并发执行测试用例：URL构建 → 变量替换 → HTTP请求(Mock) → 响应解析 → 断言。

## 输入文件
- `shared_data/step_06_test_cases.json` — 测试用例（来自 demo_06）

## 必需 pip 包
无（标准库 threading）

## 可选 pip 包
无

## 运行
```bash
cd demo_09_异步执行
python execute_tests.py
```

## 预期输出
- `shared_data/step_09_results.json` — 执行结果（pass/fail/error + 断言详情）
- 控制台打印: "N pass / M fail (通过率 X%)"
