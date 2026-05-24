# demo_02 — 文档解析引擎

从 Swagger/OpenAPI 等 10 种格式的接口文档中提取标准化接口列表。

## 输入文件
- `shared_data/sample_swagger.json` — 示例 Swagger 文档

## 必需 pip 包
```bash
pip install PyYAML
```

## 可选 pip 包
```bash
pip install PyPDF2          # PDF 解析
pip install python-docx     # Word 文档解析
```

## 运行
```bash
cd demo_02_文档解析
python parse_document.py
```

## 预期输出
- `shared_data/step_02_interfaces.json` — 标准化接口列表（含 name/method/path/service/body 等字段）
- 控制台打印: "解析接口: N 个"
