# demo_05 — 智能数据工厂

四层数据模型 (L1-L4) 生成测试数据：原子值 → 接口组装 → 链上下文 → 场景分类。

## 输入文件
- `shared_data/step_02_interfaces.json` — 接口列表
- `shared_data/step_04_dependencies.json` — 依赖分析结果

## 必需 pip 包
无（标准库即可运行，Faker 可选）

## 可选 pip 包
```bash
pip install faker    # 中文数据生成（手机号/姓名/地址等）
```

## 运行
```bash
cd demo_05_数据工厂
python generate_data.py
```

## 预期输出
- `shared_data/step_05_test_data.json` — 每个接口的 4 类测试数据（positive/boundary/negative/invalid）
- 控制台打印: "N 组测试数据"
