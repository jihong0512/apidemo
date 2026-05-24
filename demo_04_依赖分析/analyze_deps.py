"""
demo_04 依赖分析 — 入口
运行: python analyze_deps.py
输入: shared_data/step_02_interfaces.json + shared_data/step_03_knowledge_graph.json
输出: shared_data/step_04_dependencies.json
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
from core import analyze_dependencies


def main():
    print_service_status()
    print_header(4, "依赖分析 — Kahn拓扑排序 + 数据流链路推断")

    print_step(4, "依赖分析", "step_02_interfaces.json + step_03_knowledge_graph.json",
               "step_04_dependencies.json")

    interfaces_data = read_json("step_02_interfaces.json")
    kg_data = read_json("step_03_knowledge_graph.json")

    interfaces = interfaces_data["interfaces"]
    result = analyze_dependencies(interfaces, kg_data)

    # 打印执行顺序
    print("\n  执行顺序:")
    for item in result["execution_order"]:
        print(f"    Step {item['step']:2d}. {item['interface']}")

    # 打印数据流链路
    for chain in result["data_flow_chains"]:
        print(f"\n  {chain['chain_type']}: {chain['source']}")
        for dep in chain["dependents"]:
            print(f"    -> {dep}")

    if result["cycles"]:
        print(f"\n  [WARN] 检测到环形依赖: {result['cycles']}")

    write_json("step_04_dependencies.json", result)

    print_summary(4, result)
    print_success(4)


if __name__ == "__main__":
    main()
