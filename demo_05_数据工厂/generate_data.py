"""
demo_05 智能数据工厂 — 入口
运行: python generate_data.py
输入: shared_data/step_02_interfaces.json + shared_data/step_04_dependencies.json
输出: shared_data/step_05_test_data.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import (
    add_project_to_sys_path, read_json, write_json,
    print_header, print_step, print_summary, print_success,
    print_service_status, Config,
)
add_project_to_sys_path(__file__)
from core import generate_test_data, generate_pytest_parametrize_code


def main():
    print_service_status()
    print_header(5, "智能数据工厂 — Schema + 依赖 -> Faker测试数据")

    print_step(5, "数据工厂", "step_02_interfaces.json + step_04_dependencies.json",
               "step_05_test_data.json")

    interfaces_data = read_json("step_02_interfaces.json")
    deps_data = read_json("step_04_dependencies.json")

    interfaces = interfaces_data["interfaces"]
    result = generate_test_data(interfaces, deps_data)

    # 打印每个接口的测试数据概览
    print("\n  生成测试数据概览:")
    for name, cases in result["test_data"].items():
        case_types = [c["case_type"] for c in cases]
        print(f"    {name}: {len(cases)} 组用例 ({', '.join(case_types)})")

    write_json("step_05_test_data.json", result)

    # ── pytest 参数化代码生成 ──
    if Config.is_llm_available():
        try:
            pytest_code = generate_pytest_parametrize_code(result["test_data"])
            if pytest_code:
                print(f"\n  ── pytest 参数化代码示例 ──")
                print(pytest_code[:500])
                print(f"  ... (共 {len(pytest_code)} 字符)")
        except Exception:
            pass

    print_summary(5, result)
    print_success(5)


if __name__ == "__main__":
    main()
