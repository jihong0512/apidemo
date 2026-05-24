"""
demo_07 RAG知识库 — 入口
运行: python build_index.py
输入: shared_data/step_02_interfaces.json + shared_data/step_06_test_cases.json
输出: shared_data/step_07_rag_index.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import add_project_to_sys_path, read_json, write_json, print_header, print_step, print_summary, print_success
add_project_to_sys_path(__file__)
from core import build_rag_index, search_similar


def main():
    print_header(7, "RAG知识库 — 用例+接口 -> numpy向量索引")

    print_step(7, "RAG知识库", "step_02_interfaces.json + step_06_test_cases.json",
               "step_07_rag_index.json")

    interfaces_data = read_json("step_02_interfaces.json")
    cases_data = read_json("step_06_test_cases.json")

    interfaces = interfaces_data["interfaces"]
    test_cases = cases_data["test_cases"]

    rag_index = build_rag_index(interfaces, test_cases)

    # 演示检索
    test_queries = [
        "用户登录接口",
        "创建设备",
        "删除操作",
    ]
    print("\n  检索演示:")
    for query in test_queries:
        results = search_similar(query, rag_index, top_k=2)
        print(f"\n    查询: '{query}'")
        for r in results:
            doc = r["document"]
            print(f"      [{r['similarity']:.3f}] {doc['type']}: {doc['text'][:60]}...")

    write_json("step_07_rag_index.json", rag_index)

    print_summary(7, rag_index)
    print_success(7)


if __name__ == "__main__":
    main()
