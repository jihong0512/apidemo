"""
demo_09 异步执行引擎 — 入口
运行: python execute_cases.py
输入: shared_data/step_06_test_cases.json + shared_data/step_05_test_data.json
输出: shared_data/step_09_execution_results.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import add_project_to_sys_path, read_json, write_json, print_header, print_step, print_summary, print_success
add_project_to_sys_path(__file__)
from core import execute_test_cases


def main():
    print_header(9, "异步执行引擎 — threading并发 + MockResponder")

    print_step(9, "异步执行", "step_06_test_cases.json + step_05_test_data.json",
               "step_09_execution_results.json")

    cases_data = read_json("step_06_test_cases.json")
    test_cases = cases_data["test_cases"]

    result = execute_test_cases(test_cases, concurrency=3)

    # 打印执行结果
    print(f"\n  执行结果: {result['passed_count']} passed / {result['failed_count']} failed / {result['error_count']} error")
    print(f"  通过率: {result['pass_rate']}%")
    print(f"  总耗时: {result['total_elapsed']}s (并发: {result['concurrency']})")

    # 打印失败详情
    failed = [r for r in result["results"] if r["status"] != "passed"]
    if failed:
        print(f"\n  失败用例 ({len(failed)} 条):")
        for r in failed:
            print(f"    [{r['status']}] {r['test_case_id']}: expect {r.get('expected_status')}, got {r.get('actual_status')}")

    write_json("step_09_execution_results.json", result)

    print_summary(9, result)
    print_success(9)


if __name__ == "__main__":
    main()
