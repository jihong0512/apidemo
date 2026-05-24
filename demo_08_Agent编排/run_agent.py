"""
demo_08 Agent编排 — 入口
运行: python run_agent.py [任务描述]
输入: shared_data/step_02_interfaces.json + shared_data/step_07_rag_index.json
输出: shared_data/step_08_agent_cases.json

用法示例:
  python run_agent.py
  python run_agent.py "为设备管理模块生成完整CRUD测试用例"
  python run_agent.py "测试用户登录和注册流程"
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
from core import run_agent_workflow


def main():
    print_service_status()

    # ── 任务描述: 命令行参数 或 默认值 ──
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = "为设备管理模块生成完整CRUD测试用例，包括登录认证、设备创建/查询/更新/删除、以及边界值和异常场景"

    print_header(8, "Agent编排 — LangGraph StateGraph + 3-Agent协作")

    print_step(8, "Agent编排",
               "step_02_interfaces.json + step_07_rag_index.json",
               "step_08_agent_cases.json")

    # ── 加载输入数据 ──
    interfaces_data = read_json("step_02_interfaces.json")
    rag_index = read_json("step_07_rag_index.json")

    interfaces = interfaces_data["interfaces"]
    print(f"\n  可用接口总数: {len(interfaces)} 个")
    print(f"  RAG文档数: {len(rag_index.get('documents', []))} 条")

    # ── 展示接口列表 ──
    print(f"\n  ── 可用接口列表 ──")
    for i in interfaces:
        print(f"    [{i.get('method', 'GET')}] {i.get('url', '')} — {i.get('name', '')}")

    # ── 执行Agent编排 ──
    result = run_agent_workflow(task, rag_index, interfaces)

    # ── 展示编排结果 ──
    print(f"\n  ══════════════════════════════════════")
    print(f"  编排结果")
    print(f"  ──────────────────────────────────────")

    # 匹配的接口
    print(f"\n  匹配接口 ({len(result['parsed_interfaces'])} 个):")
    for iface in result["parsed_interfaces"]:
        print(f"    ✓ [{iface.get('method', 'GET')}] {iface.get('name', '')}"
              f" — {iface.get('url', '')}")

    # 执行顺序
    exec_order = result.get("execution_order", [])
    if exec_order:
        print(f"\n  推荐执行顺序:")
        for e in exec_order:
            print(f"    Step {e.get('step', '?')}: {e.get('interface', '?')}")

    # 生成的用例
    test_cases = result["test_cases"]
    print(f"\n  生成用例 ({len(test_cases)} 条):")
    for tc in test_cases:
        case_type = tc.get("type", "?")
        name = tc.get("name", "?")
        deps = tc.get("dependencies", [])
        dep_str = f" (依赖: {', '.join(deps)})" if deps else ""
        print(f"    [{case_type}] {name}{dep_str}")

    # ── 写入输出 ──
    write_json("step_08_agent_cases.json", result)

    print_summary(8, {
        "parsed_interfaces": result["parsed_interfaces"],
        "dependencies": result["dependencies"],
        "test_cases": result["test_cases"],
        "orchestration_mode": result["orchestration_mode"],
    })
    print_success(8)


if __name__ == "__main__":
    main()
