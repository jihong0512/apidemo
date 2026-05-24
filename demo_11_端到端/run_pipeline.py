"""
demo_11 端到端实战 — 全链路串联入口 (PipelineRunner 重构版)
════════════════════════════════════════════════════════════════
运行:
  python run_pipeline.py                          # 跑全部 10 步
  python run_pipeline.py --from-step 4            # 从第 4 步开始
  python run_pipeline.py --from-step 2 --to-step 6 # 只跑 2-6 步
  python run_pipeline.py --verbose                # 打印每步详细信息

效果: 从 sample_swagger.json 开始，依次跑通 demo_02 ~ demo_10 的全部逻辑

重构要点 (vs 旧版):
  - 使用 demo_common.PipelineStep 封装 read→call→write 模式
  - 每步 try/except 包裹，优雅处理异常
  - CLI 参数支持部分执行和调试
  - 自动打印步骤耗时汇总表
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import (
    add_project_to_sys_path, read_json, write_json,
    PipelineStep, PipelineRunner, SHARED_DIR,
)
add_project_to_sys_path(__file__)


def _count_methods(interfaces: list) -> dict:
    counts = {}
    for i in interfaces:
        m = i.get("method", i.get("_method", "GET"))
        counts[m] = counts.get(m, 0) + 1
    return counts


def build_pipeline(args) -> PipelineRunner:
    """构建 10 步流水线——每步是一个 PipelineStep"""

    runner = PipelineRunner(stop_on_error=not args.continue_on_error)
    interfaces_ref = []  # 闭包引用，在 step 02 中赋值

    # ── Step 02: 文档解析 ──
    def run_step_02():
        from demo_02_文档解析.core import parse_swagger_document
        swagger = read_json("sample_swagger.json")
        ifaces = parse_swagger_document(swagger)
        for idx, iface in enumerate(ifaces):
            iface["_index"] = idx
        interfaces_ref.clear()
        interfaces_ref.extend(ifaces)
        return {
            "interfaces": ifaces,
            "total_count": len(ifaces),
            "methods_summary": _count_methods(ifaces),
            "services": list(set(i.get("service", i.get("_service", "")) for i in ifaces)),
        }

    runner.add_step(PipelineStep(
        step_num=2, name="文档解析",
        input_file="sample_swagger.json",
        output_file="step_02_interfaces.json",
        runner=lambda data=None: run_step_02(),
        require_input=False,
    ))

    # ── Step 03: 知识图谱 ──
    def run_step_03():
        from demo_03_知识图谱.core import build_knowledge_graph
        ifaces = list(interfaces_ref)
        kg = build_knowledge_graph(ifaces)
        return {
            "nodes": kg["nodes"], "edges": kg["edges"],
            "node_count": kg["node_count"], "edge_count": kg["edge_count"],
            "dependency_types": kg["dependency_types"],
        }

    runner.add_step(PipelineStep(
        step_num=3, name="知识图谱", input_file=None,
        output_file="step_03_knowledge_graph.json",
        runner=lambda data=None: run_step_03(),
        require_input=False,
    ))

    # ── Step 04: 依赖分析 ──
    runner.add_step(PipelineStep(
        step_num=4, name="依赖分析",
        input_file="step_03_knowledge_graph.json",
        output_file="step_04_dependencies.json",
        runner=lambda kg_data: _run_step_04(list(interfaces_ref), kg_data),
    ))

    # ── Step 05: 数据工厂 ──
    runner.add_step(PipelineStep(
        step_num=5, name="数据工厂",
        input_file="step_04_dependencies.json",
        output_file="step_05_test_data.json",
        runner=lambda deps: _run_step_05(list(interfaces_ref), deps),
    ))

    # ── Step 06: 用例生成 ──
    runner.add_step(PipelineStep(
        step_num=6, name="用例生成",
        input_file="step_05_test_data.json",
        output_file="step_06_test_cases.json",
        runner=lambda td: _run_step_06(list(interfaces_ref), td),
    ))

    # ── Step 07: RAG知识库 ──
    runner.add_step(PipelineStep(
        step_num=7, name="RAG知识库",
        input_file="step_06_test_cases.json",
        output_file="step_07_rag_index.json",
        runner=lambda cr: _run_step_07(list(interfaces_ref), cr),
    ))

    # ── Step 08: Agent编排 ──
    runner.add_step(PipelineStep(
        step_num=8, name="Agent编排",
        input_file="step_07_rag_index.json",
        output_file="step_08_agent_cases.json",
        runner=lambda ri: _run_step_08(list(interfaces_ref), ri),
    ))

    # ── Step 09: 异步执行 ──
    runner.add_step(PipelineStep(
        step_num=9, name="异步执行",
        input_file="step_06_test_cases.json",
        output_file="step_09_execution_results.json",
        runner=lambda cr: _run_step_09(cr),
    ))

    # ── Step 10: 失败分析 ──
    runner.add_step(PipelineStep(
        step_num=10, name="失败分析",
        input_file="step_09_execution_results.json",
        output_file="step_10_analysis.json",
        runner=lambda er: _run_step_10(er),
    ))

    return runner


# ════════════════════════════════════════════════════════════════
# Step runner helpers (从 lambda 中提取，便于单独测试)
# ════════════════════════════════════════════════════════════════

def _run_step_04(interfaces, kg_data):
    from demo_04_依赖分析.core import analyze_dependencies
    return analyze_dependencies(interfaces, kg_data)


def _run_step_05(interfaces, deps_data):
    from demo_05_数据工厂.core import generate_test_data
    return generate_test_data(interfaces, deps_data)


def _run_step_06(interfaces, test_data):
    from demo_06_用例生成.core import generate_test_cases
    td = test_data.get("test_data", test_data)
    return generate_test_cases(interfaces, td)


def _run_step_07(interfaces, cases_result):
    from demo_07_RAG知识库.core import build_rag_index
    return build_rag_index(interfaces, cases_result.get("test_cases", []))


def _run_step_08(interfaces, rag_index):
    from demo_08_Agent编排.core import run_agent_workflow
    return run_agent_workflow(
        "测试设备管理模块的完整CRUD流程，包括边界值和异常场景",
        rag_index, interfaces,
    )


def _run_step_09(cases_result):
    from demo_09_异步执行.core import execute_test_cases
    return execute_test_cases(cases_result.get("test_cases", []), concurrency=3)


def _run_step_10(exec_data):
    from demo_10_失败分析.core import analyze_failures
    return analyze_failures(exec_data)


# ════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="端到端全链路测试 — 10步流水线一键串联",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_pipeline.py                      # 跑全部 10 步
  python run_pipeline.py --from-step 4        # 从依赖分析开始
  python run_pipeline.py --from-step 2 --to-step 6  # 只跑 2-6
  python run_pipeline.py --verbose            # 详细输出
  python run_pipeline.py --continue-on-error  # 单步失败不停止
        """,
    )
    parser.add_argument("--from-step", type=int, default=2,
                        help="起始步骤编号 (2-10, default: 2)")
    parser.add_argument("--to-step", type=int, default=10,
                        help="结束步骤编号 (2-10, default: 10)")
    parser.add_argument("--verbose", action="store_true",
                        help="打印每步详细输入/输出信息")
    parser.add_argument("--continue-on-error", action="store_true",
                        help="单步失败后继续执行后续步骤")

    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("  端到端全链路测试 — 10步流水线一键串联")
    print("=" * 70)
    print(f"  起点: shared_data/sample_swagger.json")
    print(f"  终点: shared_data/step_10_analysis.json")
    print(f"  范围: Step {args.from_step} → Step {args.to_step}")
    print(f"  模式: {'逐个失败继续' if args.continue_on_error else '遇错即停'}")
    print("=" * 70)

    runner = build_pipeline(args)

    # 执行流水线
    success = runner.run_all(from_step=args.from_step, to_step=args.to_step)

    # 打印结果摘要
    if runner.results:
        _print_pipeline_summary(runner, success)

    return 0 if success else 1


def _print_pipeline_summary(runner: PipelineRunner, all_success: bool):
    """打印流水线执行摘要"""
    step_labels = {
        2: "文档解析", 3: "知识图谱", 4: "依赖分析",
        5: "数据工厂", 6: "用例生成", 7: "RAG知识库",
        8: "Agent编排", 9: "异步执行", 10: "失败分析",
    }

    print("\n" + "=" * 70)
    print("  流水线执行摘要")
    print("=" * 70)
    print(f"  {'步骤':<8} {'阶段':<12} {'耗时':<10} {'状态':<8} {'结果摘要'}")
    print(f"  {'-'*66}")

    total_time = 0.0
    completed = 0
    failed = 0

    for step_num in sorted(runner.results.keys()):
        result = runner.results[step_num]
        label = step_labels.get(step_num, f"Step {step_num}")
        elapsed = result.get("elapsed", 0.0)
        total_time += elapsed
        status = result.get("status", "unknown")

        if status == "success":
            completed += 1
            status_icon = "✓ OK"
        else:
            failed += 1
            status_icon = "✗ FAIL"

        summary = result.get("summary", result.get("error", ""))
        if len(str(summary)) > 40:
            summary = str(summary)[:37] + "..."

        print(f"  step_{step_num:02d}  {label:<12} {elapsed:>6.2f}s   {status_icon:<8} {summary}")

    print(f"  {'-'*66}")
    print(f"  {'合计':<8} {'':<12} {total_time:>6.2f}s   {'':<8} {completed} 完成 / {failed} 失败")
    print("=" * 70)
    print(f"  状态: {'全部通过' if all_success else '有步骤失败'}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    sys.exit(main())
