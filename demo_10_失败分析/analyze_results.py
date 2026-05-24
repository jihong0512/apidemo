"""
demo_10 失败分析 — 入口
运行: python analyze_results.py
输入: shared_data/step_09_execution_results.json
输出: shared_data/step_10_analysis.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import add_project_to_sys_path, read_json, write_json, print_header, print_step, print_summary, print_success
add_project_to_sys_path(__file__)
from core import analyze_failures


def main():
    print_header(10, "失败分析 — 三层分类 + 根因推断 + 修复建议")

    print_step(10, "失败分析", "step_09_execution_results.json",
               "step_10_analysis.json")

    execution_result = read_json("step_09_execution_results.json")
    result = analyze_failures(execution_result)

    # 打印分析结果
    print(f"\n  {result.get('summary_text', str(result.get('summary', '')))}")

    if result["analysis"]:
        print(f"\n  失败用例分析:")
        for a in result["analysis"]:
            print(f"    [{a['category_cn']}] {a['test_case']}")
            print(f"      根因: {a['root_cause']}")
            print(f"      建议: {a['suggested_fix'][:80]}...")

    if result["suggestions"]:
        print(f"\n  修复建议 (按优先级):")
        for s in result["suggestions"]:
            print(f"    [{s['priority']}] {s['action']}: {s['detail']}")

    write_json("step_10_analysis.json", result)

    print_summary(10, result)
    print_success(10)


if __name__ == "__main__":
    main()
