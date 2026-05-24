"""
demo_06 用例生成引擎 — 入口
运行: python generate_cases.py
输入: shared_data/step_02_interfaces.json + shared_data/step_05_test_data.json
输出: shared_data/step_06_test_cases.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import (
    add_project_to_sys_path, read_json, write_json,
    print_header, print_step, print_summary, print_success,
    print_service_status,
)
add_project_to_sys_path(__file__)
from core import generate_test_cases


def main():
    print_service_status()
    print_header(6, "用例生成引擎 — 接口Schema + 测试数据 -> pytest代码")

    print_step(6, "用例生成", "step_02_interfaces.json + step_05_test_data.json",
               "step_06_test_cases.json")

    interfaces_data = read_json("step_02_interfaces.json")
    test_data = read_json("step_05_test_data.json")

    interfaces = interfaces_data["interfaces"]
    result = generate_test_cases(interfaces, test_data["test_data"])

    # 打印示例代码
    print("\n  生成用例示例:")
    for tc in result["test_cases"][:3]:
        print(f"\n  --- {tc['function_name']} ({tc['case_type']}) ---")
        for line in tc["pytest_code"].split("\n")[:6]:
            print(f"    {line}")
        if len(tc["pytest_code"].split("\n")) > 6:
            print(f"    ... (共 {len(tc['pytest_code'].split(chr(10)))} 行)")

    write_json("step_06_test_cases.json", result)

    print_summary(6, result)
    print_success(6)


if __name__ == "__main__":
    main()
