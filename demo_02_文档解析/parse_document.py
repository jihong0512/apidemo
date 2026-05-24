"""
demo_02 文档解析引擎 — 入口
运行: python parse_document.py
输入: shared_data/sample_swagger.json
输出: shared_data/step_02_interfaces.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import (
    add_project_to_sys_path, read_json, write_json,
    print_header, print_step, print_summary, print_success,
)
add_project_to_sys_path(__file__)
from core import parse_swagger_document, detect_format


def main():
    print_header(2, "文档解析引擎 — Swagger文档 → 标准化接口列表")

    print_step(2, "文档解析", "sample_swagger.json", "step_02_interfaces.json")

    # 读取 Swagger 文档
    swagger_data = read_json("sample_swagger.json")

    # 解析接口
    interfaces = parse_swagger_document(swagger_data)

    # 补充解析元信息
    for i, iface in enumerate(interfaces):
        iface["_index"] = i
        # 检查是否需要认证
        if "Authorization" in str(iface.get("headers", {})):
            iface["requires_auth"] = True

    # 写入产出
    output = {
        "interfaces": interfaces,
        "total_count": len(interfaces),
        "methods_summary": _summarize_methods(interfaces),
        "services": list(set(i["service"] for i in interfaces)),
    }
    # ── 多格式解析演示 ──
    # 展示 parse_document() 的多格式检测与路由能力
    print(f"\n  ── 多格式解析演示 ──")
    sample_texts = [
        ('{"swagger": "2.0", "paths": {"/api/test": {"get": {"summary": "测试接口"}}}}', "Swagger 2.0"),
        ('{"openapi": "3.0.0", "paths": {"/api/test": {"get": {"summary": "测试接口"}}}}', "OpenAPI 3.0"),
    ]
    for sample, fmt_name in sample_texts:
        fmt = detect_format(sample)
        print(f"    {fmt_name}: detect_format → '{fmt}'")
    print(f"    共支持 10 种格式: Swagger2.0/3.0, OpenAPI3.x, PDF, Word, JMX, Apifox, Postman, CURL, HAR, 纯文本")

    write_json("step_02_interfaces.json", output)

    print_summary(2, output)
    print_success(2)


def _summarize_methods(interfaces: list) -> dict:
    """统计 HTTP 方法分布"""
    counts = {}
    for i in interfaces:
        m = i["method"]
        counts[m] = counts.get(m, 0) + 1
    return counts


if __name__ == "__main__":
    main()
