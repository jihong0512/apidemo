"""
mini_graph_demo.py — LangGraph 六大 API 最小示例
═══════════════════════════════════════════════════════════
对应课件: 第08讲「LangGraph 六大 API」讲解之后

目的: 用 15 行代码展示 LangGraph 六个核心 API 怎么配合
      把一个字符串转成大写——虽然功能简单, 但 StateGraph
      定义、节点函数签名、增量合并、编译验证这些模式和
      后面 315 行的 Agent 编排器完全一致。

六大 API:
  ① StateGraph(dict)    — 创建状态图, dict 是共享状态
  ② add_node()          — 添加处理节点
  ③ set_entry_point()   — 指定入口
  ④ add_edge()          — 添加流转边
  ⑤ compile()           — 编译验证图结构
  ⑥ invoke()            — 同步执行 (课堂上先看 invoke, 生产用 ainvoke)

安装: pip install langgraph
运行: python mini_graph_demo.py
═══════════════════════════════════════════════════════════
"""


def main():
    print("=" * 55)
    print("  LangGraph 六大 API 最小示例")
    print("=" * 55)

    # ── 第 1 步: 尝试导入 LangGraph ──
    try:
        from langgraph.graph import StateGraph, END
        print("\n  [OK] LangGraph 已安装, 走主路径")
    except ImportError:
        print("\n  [X] LangGraph 未安装, 运行降级演示:")
        print("    pip install langgraph 即可启用完整功能")
        _demo_fallback()
        return

    # ── 第 2 步: 定义共享状态类型 ──
    # 用 python dict, 两个字段: text (原始字符串) 和 processed (处理结果)
    # 正式项目建议用 TypedDict, 有 IDE 补全 + mypy 检查
    from typing import TypedDict

    class MyState(TypedDict):
        text: str
        processed: str

    # ── 第 3 步: 定义节点函数 ──
    # 签名必须是: (State) → State
    # 注意: 只更新 processed 字段, text 保持原值
    # 这就是 LangGraph 的增量合并——返回什么就更新什么
    def uppercase_node(state: MyState) -> MyState:
        return {"processed": state["text"].upper()}

    def reverse_node(state: MyState) -> MyState:
        return {"processed": state["text"][::-1]}

    # ── 第 4 步: 构建状态图 (六大 API 全部用到) ──
    print("\n  ── 构建状态图 ──")
    print("  ① StateGraph(MyState) — 创建状态图")
    print("  ② add_node — 添加两个节点")
    print("  ③ set_entry_point — 指定入口为 uppercase")
    print("  ④ add_edge — uppercase → reverse → END")
    print("  ⑤ compile — 编译验证图结构")

    workflow = StateGraph(MyState)                # ① 创建状态图
    workflow.add_node("uppercase", uppercase_node) # ② 添加节点
    workflow.add_node("reverse", reverse_node)
    workflow.set_entry_point("uppercase")          # ③ 指定入口
    workflow.add_edge("uppercase", "reverse")      # ④ 添加边
    workflow.add_edge("reverse", END)
    app = workflow.compile()                       # ⑤ 编译

    # ── 第 5 步: 执行状态图 ──
    print("\n  ── 执行状态图 ──")
    print("  ⑥ invoke — 传入初始状态, 跑完所有节点")

    result = app.invoke({"text": "hello world"})  # ⑥ 执行

    print(f"\n  ── 结果 ──")
    print(f"  输入:    \"hello world\"")
    print(f"  uppercase 节点输出: {result['text'].upper()}")
    print(f"  reverse 节点输出:  {result['text'][::-1]}")
    print(f"  最终 State:")
    print(f"    text = \"{result['text']}\"")
    print(f"    processed = \"{result['processed']}\"")

    # ── 第 6 步: 演示增量合并 ──
    print(f"\n  ── 增量合并验证 ──")
    print(f"  text 字段从始至终没变: \"{result['text']}\" ← 原始值")
    print(f"  processed 字段被 reverse_node 更新了")
    print(f"  这就是 LangGraph 的核心机制:")
    print(f"    节点函数返回什么字段, 就更新什么字段")
    print(f"    没返回的字段, 保持原值不变")
    print(f"    → Agent 互不干扰的基石!")

    print(f"\n  ── 和 315 行 Agent 编排器的关系 ──")
    print(f"  这个 15 行例子里的 6 个 API,")
    print(f"  和 MultiAgentOrchestrator._build_workflow() 里的用法")
    print(f"  完全一致——只是节点从 2 个变成了 3 个,")
    print(f"  State 从 2 个字段变成了 7 个。")

    # ── 第 7 步: ainvoke 异步演示 ──
    print("\n  ── ⑦ ainvoke 异步演示 ──")
    print("  (LangGraph 支持 async/await, 与 FastAPI 完美配合)")

    import asyncio

    async def demo_ainvoke():
        """异步执行 — FastAPI 事件循环中不阻塞"""
        result = await app.ainvoke({"text": "async hello"})
        print(f"  ainvoke 结果: processed = \"{result['processed']}\"")
        return result

    try:
        asyncio.run(demo_ainvoke())
    except RuntimeError:
        # 如果已有事件循环在运行（Jupyter等环境）
        print("  (当前环境已有事件循环, ainvoke 演示跳过)")
    except Exception as e:
        print(f"  ainvoke 演示异常: {e} (不影响主流程)")


def _demo_fallback():
    """LangGraph 不可用时的演示说明"""
    print("""\n  ══════════════════════════════════════════
    降级说明:

    LangGraph 的核心概念在纯 Python 中也是成立的:

    ① StateGraph    = 一个 dict, 在函数之间传递
    ② add_node      = 定义一个函数
    ③ set_entry_point = 决定先从哪个函数开始
    ④ add_edge      = 决定 A 执行完执行 B
    ⑤ compile       = 验证所有函数签名是否匹配
    ⑥ invoke        = 逐节点执行

    安装 LangGraph 后运行:
      pip install langgraph
      python mini_graph_demo.py

    就能看到真实的图执行效果。
    ══════════════════════════════════════════""")


if __name__ == "__main__":
    main()
