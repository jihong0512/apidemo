"""
demo_03 知识图谱 — 入口
运行: python build_graph.py
输入: shared_data/step_02_interfaces.json
输出: shared_data/step_03_knowledge_graph.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import add_project_to_sys_path, read_json, write_json, print_header, print_step, print_summary, print_success
add_project_to_sys_path(__file__)
from core import build_knowledge_graph, query_graph_natural_language


def main():
    print_header(3, "知识图谱构建 — 接口列表 -> networkx 有向图")

    print_step(3, "知识图谱", "step_02_interfaces.json", "step_03_knowledge_graph.json")

    data = read_json("step_02_interfaces.json")
    interfaces = data["interfaces"]

    kg = build_knowledge_graph(interfaces)

    # 序列化网络图（networkx 不能直接 JSON 序列化）
    graph_data = {
        "nodes": kg["nodes"],
        "edges": kg["edges"],
        "node_count": kg["node_count"],
        "edge_count": kg["edge_count"],
        "dependency_types": kg["dependency_types"],
        # 图的结构信息（供 networkx 重建）
        "adjacency": _serialize_adjacency(kg["graph"]),
    }

    write_json("step_03_knowledge_graph.json", graph_data)

    # ── Cypher 查询示例展示 ──
    from core import CYPHEER_QUERY_EXAMPLES
    print(f"\n  ── Cypher 查询示例（共 {len(CYPHEER_QUERY_EXAMPLES)} 个）──")
    for i, (name, query) in enumerate(CYPHEER_QUERY_EXAMPLES.items()):
        if i < 3:  # 只展示前3个
            print(f"    {i+1}. {name}")
            print(f"       {query[:100]}...")
    print(f"    完整 15 个 Cypher 示例见 core.py 中的 CYPHEER_QUERY_EXAMPLES 字典")

    # ── 自然语言查询演示 ──
    print(f"\n  ── 自然语言查询演示 ──")
    demo_questions = [
        "login接口依赖哪些接口？",
        "列出所有孤立节点",
        "按服务分组展示接口",
    ]
    for q in demo_questions:
        result = query_graph_natural_language(q)
        cypher_len = len(result.get("cypher", ""))
        records = len(result.get("results", []))
        status = "✓" if not result.get("error") else "✗"
        print(f"    {status} Q: {q}")
        print(f"       Cypher ({cypher_len} chars) → {records} 条结果")
        if result.get("error"):
            print(f"       降级原因: {result['error'][:80]}")

    print_summary(3, graph_data)
    print_success(3)


def _serialize_adjacency(G) -> dict:
    """序列化图的邻接关系（dict 格式，供下游 demo 读取）"""
    adj = {}
    for node in G.nodes():
        adj[node] = list(G.successors(node))
    return adj


if __name__ == "__main__":
    main()
