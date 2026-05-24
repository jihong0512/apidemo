"""
demo_08 Agent编排 — 核心逻辑 (重构版 v4.0)
════════════════════════════════════════════════════════════════
对应课件: 第08讲 Agent编排系统 — LangGraph工作流与多Agent协作

真实编排: 将前序 demo 的函数通过 LangGraph StateGraph 串联:
  demo_07 RAG检索 → demo_02 接口解析 → demo_04 依赖分析 → demo_06 用例生成

架构: "外层Workflow编排 + 内层Agent智能决策"
  - Workflow层: StateGraph 线性编排（parser → analyzer → generator）
  - Agent层: 每个节点内 LLM 自主决策，通过 AgentState 黑板共享数据

三个Agent:
  InterfaceParserAgent:    用户任务+RAG检索 → LLM筛选匹配接口 → 输出parsed_interfaces
  DependencyAnalyzerAgent: 接口列表 → demo_04.analyze_dependencies() → 输出dependencies
  TestCaseGeneratorAgent:  接口+依赖+RAG → LLM生成四类用例 → 输出test_cases

Agent间通信: 通过 AgentState 黑板共享数据，不直接调用

⚠ 课件 vs Demo 差异:
  课件使用三个 Agent 类 (InterfaceParserAgent / DependencyAnalyzerAgent /
  TestCaseGeneratorAgent) 各自实现 (State) → State 方法签名。
  本 demo 使用三个函数 (_run_parser_agent / _run_dependency_agent /
  _run_generator_agent) + AgentState 类实例（非课件中的 TypedDict）。
  架构等价：函数式减少了样板代码，对教学更友好；AgentState 类 vs TypedDict
  不影响运行时行为。生产版 (agent_service.py) 使用类模式以支持依赖注入和多用户并发。
════════════════════════════════════════════════════════════════
"""
import sys
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import (
    add_project_to_sys_path, Config, llm_client, neo4j_manager,
)
add_project_to_sys_path(__file__)


# ══════════════════════════════════════════════════════════════
# AgentState: 多Agent共享状态 (黑板架构)
# 对应 agent_service.py L12-20 AgentState TypedDict
#
# 为什么用"黑板架构"而不是Agent直接调用?
#   - 解耦: 每个Agent只读写State, 不关心其他Agent的实现
#   - 可观测: 所有中间结果都在State中, 方便日志/调试
#   - 可扩展: 添加新Agent只需新增add_node, 不影响现有Agent
# ══════════════════════════════════════════════════════════════

class AgentState:
    """
    多Agent共享状态 (黑板架构)

    数据流: current_task → parsed_interfaces → dependencies → test_cases
    每个Agent只读写自己负责的字段, 通过State解耦
    """
    def __init__(self, task: str = ""):
        self.messages: list = []            # 对话历史
        self.current_task: str = task       # 用户任务描述 ("为支付模块生成测试用例")
        self.parsed_interfaces: list = []   # Parser输出 → Analyzer输入
        self.dependencies: dict = {}        # Analyzer输出 → Generator输入
        self.test_cases: list = []          # 最终产出
        self.context: dict = {"workflow_log": []}  # RAG上下文 + 执行日志
        self.project_id: str = ""           # 项目标识 — 贯穿所有查询的租户标识

    def log(self, agent: str, **kwargs):
        """记录Agent执行日志"""
        entry = {"agent": agent, **kwargs}
        self.context.setdefault("workflow_log", []).append(entry)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    @classmethod
    def from_dict(cls, d: dict, task: str = "") -> "AgentState":
        state = cls(task)
        for k, v in d.items():
            if hasattr(state, k):
                setattr(state, k, v)
        return state


# ══════════════════════════════════════════════════════════════
# Agent 1: InterfaceParserAgent — 接口解析
# 对应 agent_service.py L23-90 InterfaceParserAgent.parse()
#
# 职责: 接收自然语言任务 + RAG检索上下文 → LLM提取/匹配相关接口
# 核心问题不是"怎么调LLM", 而是"给LLM喂什么上下文让它准确筛选"
# ══════════════════════════════════════════════════════════════

def _run_parser_agent(
    state: AgentState,
    all_interfaces: List[Dict],
    rag_index: Dict
) -> List[Dict]:
    """
    接口解析Agent

    流程:
      1. RAG检索 — 用任务描述搜索相关文档片段
      2. LLM筛选 — 从全部接口中识别与任务相关的接口
      3. 规则兜底 — LLM不可用时用关键词匹配

    数据来源:
      - RAG: demo_07_RAG知识库 → search_similar() (ChromaDB+BM25+Reranker)
    """
    task = state.current_task

    # ── 第①步: RAG上下文检索 ──
    # 用任务描述作为query, 从RAG索引中搜索最相关的接口文档/用例片段
    rag_context = ""
    if rag_index and rag_index.get("documents"):
        try:
            from demo_07_RAG知识库.core import search_similar
            rag_results = search_similar(task, rag_index, top_k=5)
            rag_context = "\n".join([
                f"- {r['document']['text'][:200]}" for r in rag_results
            ])
        except Exception as e:
            rag_context = f"(RAG检索失败: {e})"

    # ── 第②步: 构建接口摘要 ──
    # 把所有可用接口的method+url+name列出来, LLM从中筛选
    interfaces_summary = "\n".join([
        f"[{i.get('method', 'GET')}] {i.get('url', '')} — {i.get('name', '')}"
        for i in all_interfaces
    ])

    parsed = []

    # ── 路径1: LLM解析 (主路径) ──
    if Config.is_llm_available():
        prompt = f"""你是一个专业的API测试专家。用户描述了一个测试任务，你需要从可用接口列表中选择与任务相关的接口。

【用户任务】
{task}

【可用接口列表】
{interfaces_summary}

【RAG检索到的相关上下文】
{rag_context if rag_context else "无相关上下文"}

【要求】
1. 从可用接口列表中选出与任务相关的接口
2. 如果有POST/创建类接口，确保同时选中其依赖的GET/查询类接口
3. 如果任务涉及"完整流程"，选中该业务流程所需的全部接口
4. 返回JSON: {{"interfaces": ["接口名称1", "接口名称2", ...]}}

只返回JSON，不要其他内容。"""
        llm_result = llm_client.extract_json(prompt, temperature=0.3)
        if llm_result and "interfaces" in llm_result:
            selected_names = llm_result["interfaces"]
            # 按名称匹配实际接口对象
            name_map = {i["name"]: i for i in all_interfaces}
            parsed = [name_map[n] for n in selected_names if n in name_map]
            state.log("InterfaceParserAgent", mode="llm",
                      task=task, selected=selected_names, found=len(parsed))

    # ── 路径2: 规则匹配兜底 (LLM不可用时) ──
    if not parsed:
        parsed = _keyword_match_interfaces(task, all_interfaces)
        state.log("InterfaceParserAgent", mode="keyword_match",
                  task=task, found=len(parsed))

    state.parsed_interfaces = parsed
    state.context["parser_rag_context"] = rag_context
    return parsed


def _keyword_match_interfaces(task: str, interfaces: List[Dict]) -> List[Dict]:
    """
    关键词匹配兜底: 从任务描述中提取中文关键词, 在接口name/url中匹配

    为什么需要这个兜底?
      - LLM API可能不可用 (未配置Key、网络超时、配额耗尽)
      - 关键词匹配100%可靠, 不受外部服务波动影响
      - 虽不如LLM精准, 但能保证系统不崩溃
    """
    text = task.lower()

    # 关键词 → 接口名/URL关键词映射
    intent_map = {
        "登录": ["login", "auth", "token"],
        "注册": ["register", "signup"],
        "设备": ["device"],
        "创建": ["create", "post"],
        "查询": ["get", "list", "query"],
        "更新": ["update", "put", "edit"],
        "删除": ["delete", "remove"],
        "用户": ["user", "account", "profile"],
        "商品": ["product", "item"],
        "订单": ["order"],
        "支付": ["payment", "pay"],
        "课程": ["course"],
        "家庭": ["family"],
        "计划": ["plan"],
        "上传": ["upload"],
    }

    # 提取任务中的关键词
    active_keywords = []
    for kw, mapped in intent_map.items():
        if kw in text:
            active_keywords.extend(mapped)

    if not active_keywords:
        # 无匹配关键词 → 使用更宽松的匹配: 匹配URL包含通用模式的所有接口
        # 避免因为任务描述用词和数据不匹配导致空结果
        scored = [(i, 1) for i in interfaces]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [item[0] for item in scored]

    active_keywords = list(set(active_keywords))

    # 按关键词命中次数评分排序
    scored = []
    for iface in interfaces:
        score = 0
        name_lower = iface.get("name", "").lower()
        url_lower = iface.get("url", "").lower()
        method_lower = iface.get("method", "").lower()

        for kw in active_keywords:
            if kw in name_lower:
                score += 3   # 名称匹配权重最高
            if kw in url_lower:
                score += 2   # URL匹配次之
            if kw == method_lower:
                score += 1   # HTTP方法匹配

        if score >= 1:
            scored.append((iface, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [item[0] for item in scored]


# ══════════════════════════════════════════════════════════════
# Agent 2: DependencyAnalyzerAgent — 依赖分析
# 对应 agent_service.py L93-157 DependencyAnalyzerAgent.analyze()
#
# 职责: 接收接口列表 → 调用 demo_04.analyze_dependencies() → 输出依赖图
# ══════════════════════════════════════════════════════════════

def _run_dependency_agent(state: AgentState) -> Dict:
    """
    依赖分析Agent

    流程:
      1. 读取 state.parsed_interfaces (ParserAgent的输出)
      2. 调用 demo_04.analyze_dependencies() — Kahn拓扑排序+环检测+分组
      3. 如果有Neo4j, 优先从图谱获取已知关系
      4. 写入 state.dependencies

    真实集成: demo_04_依赖分析.core.analyze_dependencies()
      - Kahn拓扑排序: 入度表+BFS逐层剥离
      - 数据流推断: token_flow (登录→全部) / id_flow (创建→CRUD)
      - DFS三色环检测: WHITE(0)/GRAY(1)/BLACK(2)
      - 32组业务分组: 关键词+版本隔离+相似度兜底
    """
    interfaces = state.parsed_interfaces

    if not interfaces:
        state.dependencies = {
            "call_dependencies": [],
            "data_dependencies": [],
            "business_dependencies": [],
            "execution_order": [],
        }
        state.log("DependencyAnalyzerAgent", mode="skipped", reason="no_interfaces")
        return {}

    # ── 尝试从Neo4j获取知识图谱关系 ──
    kg_data = None
    if neo4j_manager.is_available():
        try:
            session = neo4j_manager.get_session()
            if session:
                result = session.run(
                    "MATCH (n)-[r]->(m) RETURN n.name as source, "
                    "type(r) as rel_type, m.name as target LIMIT 50"
                )
                kg_data = {
                    "relationships": [
                        {"source": r["source"], "type": r["rel_type"], "target": r["target"]}
                        for r in result
                    ]
                }
                session.close()
        except Exception as e:
            state.log("DependencyAnalyzerAgent", neo4j_warning=str(e))

    # ── 调用 demo_04 的依赖分析引擎 ──
    try:
        from demo_04_依赖分析.core import analyze_dependencies
        dep_result = analyze_dependencies(interfaces, kg_data)
    except ImportError:
        # demo_04 不可用 → LLM分析 或 规则兜底
        dep_result = _llm_dependency_analysis(state, interfaces)

    # ── 归一化: 将 demo_04 的输出映射到 Agent 标准格式 ──
    call_deps = []
    data_deps = []
    business_deps = []

    # demo_04 的 dependency_map 是 {接口名: [{target, type, weight}, ...]}
    dep_map = dep_result.get("dependency_map", {})
    for source, targets in dep_map.items():
        for t in targets:
            call_deps.append({
                "source": source,
                "target": t.get("depends_on", ""),
                "type": t.get("type", "data_flow"),
                "description": t.get("description", ""),
                "extract_fields": t.get("extract_fields", []),
            })

    # demo_04 的 data_flow_chains 是 [{chain: [{from, to, flow_type}], ...}]
    for chain_info in dep_result.get("data_flow_chains", []):
        for link in chain_info.get("chain", []):
            data_deps.append({
                "source": link.get("from", ""),
                "target": link.get("to", ""),
                "data_flow": link.get("flow_type", ""),
            })

    # 执行顺序: [{step: N, interface: "接口名"}, ...]
    exec_order = dep_result.get("execution_order", [])

    dependencies = {
        "call_dependencies": call_deps,
        "data_dependencies": data_deps,
        "business_dependencies": business_deps,
        "execution_order": exec_order,
        "groups": dep_result.get("groups", {}),
        "cycles_detected": dep_result.get("cycles_detected", False),
        "total_dependencies": dep_result.get("total_dependencies", 0),
    }

    state.dependencies = dependencies
    state.log("DependencyAnalyzerAgent", mode="demo_04_engine",
              deps_found=len(call_deps) + len(data_deps),
              groups=len(dep_result.get("groups", {})))
    return dependencies


def _llm_dependency_analysis(state: AgentState, interfaces: List[Dict]) -> Dict:
    """LLM依赖分析 (demo_04不可用时的降级路径)"""
    if not Config.is_llm_available():
        return _rule_based_dependency_analysis(interfaces)

    iface_brief = json.dumps([{
        "name": i.get("name", ""),
        "method": i.get("method", ""),
        "url": i.get("url", ""),
    } for i in interfaces], ensure_ascii=False, indent=2)

    prompt = f"""分析以下接口之间的依赖关系:

接口列表: {iface_brief}

请分析:
1. 调用依赖: 哪些接口需要其他接口的响应数据 (如业务接口需要登录接口的token)
2. 数据流: 参数如何在接口间传递 (如创建接口返回的ID → 查询/删除接口)
3. 执行顺序: 按依赖关系排列

返回JSON:
{{"call_dependencies": [{{"source":"A", "target":"B", "type":"response_dependency"}}],
 "data_dependencies": [{{"source":"A", "target":"B", "data_flow":"token传递"}}],
 "execution_order": [{{"step":1, "interface":"接口名"}}]}}"""
    result = llm_client.extract_json(prompt, temperature=0.3)
    if result:
        state.log("DependencyAnalyzerAgent", mode="llm")
        return {
            "dependency_map": {},
            "data_flow_chains": [],
            "execution_order": result.get("execution_order", []),
            "groups": {},
            "cycles_detected": False,
            "total_dependencies": len(result.get("call_dependencies", [])),
            # 同时传递LLM的原始结果供下游使用
            "call_dependencies": result.get("call_dependencies", []),
            "data_dependencies": result.get("data_dependencies", []),
        }
    return _rule_based_dependency_analysis(interfaces)


def _rule_based_dependency_analysis(interfaces: List[Dict]) -> Dict:
    """
    规则依赖分析 (LLM不可用时的兜底)

    核心规则:
      1. 所有非登录接口都依赖登录接口 (auth dependency)
      2. CRUD 接口按URL前缀分组: POST→GET→PUT→DELETE
      3. URL包含相同路径前缀的接口视为同一资源组
    """
    # 找到登录接口
    login_iface = None
    for i in interfaces:
        url = i.get("url", "").lower()
        if "login" in url or "auth" in url:
            login_iface = i
            break

    call_deps = []
    data_deps = []

    # 规则1: 所有非登录接口依赖登录接口
    if login_iface:
        login_name = login_iface.get("name", "")
        for i in interfaces:
            if i != login_iface:
                call_deps.append({
                    "source": login_name, "target": i.get("name", ""),
                    "type": "auth_dependency",
                })
                data_deps.append({
                    "source": login_name, "target": i.get("name", ""),
                    "data_flow": "response.token → request.Authorization",
                })

    # 规则2: 按URL路径前缀分组推断CRUD顺序
    from collections import defaultdict
    path_groups = defaultdict(list)
    for i in interfaces:
        url = i.get("url", "")
        parts = url.strip("/").split("/")
        prefix = "/".join(parts[:3]) if len(parts) >= 3 else "/".join(parts)
        path_groups[prefix].append(i)

    method_order = {"POST": 0, "GET": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}
    exec_order = []
    step = 1

    # 登录接口排最前
    if login_iface:
        exec_order.append({"step": step, "interface": login_iface.get("name", "")})
        step += 1

    for prefix, group in path_groups.items():
        if len(group) >= 2:
            group.sort(key=lambda x: method_order.get(x.get("method", ""), 99))
        for iface in group:
            if iface != login_iface:
                exec_order.append({"step": step, "interface": iface.get("name", "")})
                step += 1

    return {
        "dependency_map": {},
        "data_flow_chains": [],
        "execution_order": exec_order,
        "groups": {},
        "cycles_detected": False,
        "total_dependencies": len(call_deps),
        "call_dependencies": call_deps,
        "data_dependencies": data_deps,
    }


# ══════════════════════════════════════════════════════════════
# Agent 3: TestCaseGeneratorAgent — 用例生成
# 对应 agent_service.py L160-245 TestCaseGeneratorAgent.generate()
#
# 职责: 接收接口+依赖+RAG → LLM生成四类测试用例
#   ① 正常场景 ② 边界值 ③ 异常场景 ④ 依赖场景
# ══════════════════════════════════════════════════════════════

def _run_generator_agent(state: AgentState, rag_index: Dict) -> List[Dict]:
    """
    测试用例生成Agent

    流程:
      1. 读取 state.parsed_interfaces + state.dependencies
      2. RAG检索 — 搜索历史用例最佳实践
      3. per-interface循环 — 为每个接口生成四类用例
      4. extend累加 — 不是赋值, 是追加!

    为什么per-interface循环而非一次生成全部?
      - 单次Prompt太长会超过LLM上下文窗口
      - 每个接口独立生成, 一个失败不影响其他
      - 可以针对性注入该接口的依赖信息
    """
    interfaces = state.parsed_interfaces
    dependencies = state.dependencies

    if not interfaces:
        state.test_cases = []
        state.log("TestCaseGeneratorAgent", mode="skipped", reason="no_interfaces")
        return []

    # ── RAG上下文: 搜索历史用例最佳实践 ──
    rag_context = ""
    if rag_index and rag_index.get("documents"):
        try:
            from demo_07_RAG知识库.core import search_similar
            rag_results = search_similar(
                f"测试用例生成 {state.current_task}", rag_index, top_k=3
            )
            rag_context = "\n".join([
                f"- {r['document']['text'][:200]}" for r in rag_results
            ])
        except Exception:
            pass

    # ── 构建执行顺序上下文 ──
    exec_order = dependencies.get("execution_order", [])
    exec_context = "\n".join([
        f"  Step {e['step']}: {e['interface']}" for e in exec_order
    ]) if exec_order else "无特定执行顺序"

    test_cases = []

    # ── 路径1: LLM生成 ──
    if Config.is_llm_available():
        # per-interface循环: 每个接口独立调LLM
        for interface in interfaces[:8]:  # demo中限制8个, 控制Token消耗
            # 找到该接口的相关依赖
            related_deps = []
            for dep_list_name in ["call_dependencies", "data_dependencies"]:
                for dep in dependencies.get(dep_list_name, []):
                    if interface.get("name") in (dep.get("source"), dep.get("target")):
                        related_deps.append(dep)

            prompt = f"""你是一个专业的API测试用例生成专家。请为以下接口生成测试用例。

【接口信息】
名称: {interface.get('name', '')}
方法: {interface.get('method', 'GET')}
URL: {interface.get('url', '')}
描述: {interface.get('description', '')}

【依赖关系】
{json.dumps(related_deps, ensure_ascii=False, indent=2) if related_deps else "无依赖"}

【推荐执行顺序】
{exec_context}

【历史参考 (RAG)】
{rag_context if rag_context else "无历史参考"}

请生成四类测试用例:
1. 正常场景: 合法参数, 期望200/201
2. 边界值: 空值/超长/特殊字符
3. 异常场景: 缺必填/无效token/资源不存在
4. 依赖场景: 如果该接口依赖其他接口 (需先执行前置接口获取数据)

返回纯JSON:
{{"interface_name": "{interface.get('name', '')}",
 "test_cases": [
   {{"name": "用例名称", "type": "normal|boundary|exception|dependency",
     "description": "描述", "test_data": {{"params": {{}}, "headers": {{}}, "body": {{}}}},
     "assertions": [{{"type": "status_code", "expected": 200}}],
     "dependencies": []}}
 ]}}"""
            llm_result = llm_client.extract_json(prompt, temperature=0.5, max_tokens=3000)
            if llm_result and "test_cases" in llm_result:
                # extend累加——不是赋值!
                test_cases.extend(llm_result["test_cases"])

    # ── 路径2: 模板生成兜底 ──
    if not test_cases:
        test_cases = _template_generate_cases(interfaces, dependencies)

    mode = "llm" if Config.is_llm_available() and test_cases and not _is_template_mode(test_cases) else "template"
    state.test_cases = test_cases
    state.log("TestCaseGeneratorAgent", mode=mode, cases_generated=len(test_cases))
    return test_cases


def _is_template_mode(cases: List[Dict]) -> bool:
    """检查是否来自模板模式 (模板case_type含特殊后缀)"""
    return any(c.get("case_type", "") in ("compound", "boundary_extended", "idempotent")
               for c in cases)


def _template_generate_cases(
    interfaces: List[Dict],
    dependencies: Dict
) -> List[Dict]:
    """
    模板生成测试用例 (LLM不可用时的兜底)

    策略:
      - 正常场景: 每个POST/PUT接口生成一个基础正向用例
      - 边界场景: 超长字符串 + 特殊字符
      - 异常场景: 缺少必填参数
      - 请求体从接口schema中自动提取
    """
    cases = []

    for iface in interfaces:
        method = iface.get("method", "GET")
        name = iface.get("name", "")
        url = iface.get("url", "")
        body_schema = iface.get("body", {}).get("schema", {})
        required_fields = body_schema.get("required", [])
        properties = body_schema.get("properties", {})

        # 从schema自动构造请求体
        def _build_body(fill_all: bool = True) -> dict:
            body = {}
            for field, info in properties.items():
                if fill_all or field in required_fields:
                    example = info.get("example", "")
                    field_type = info.get("type", "string")
                    if field_type == "integer":
                        body[field] = example if example else 1
                    elif field_type == "boolean":
                        body[field] = example if example else True
                    else:
                        body[field] = example if example else f"test_{field}"
            return body

        # ① 正常场景
        if method in ("POST", "PUT", "PATCH"):
            cases.append({
                "name": f"正常场景: {name}",
                "type": "normal",
                "description": f"正向测试: {name} — 合法参数",
                "test_data": {
                    "params": {},
                    "headers": {"Content-Type": "application/json"},
                    "body": _build_body(fill_all=True),
                },
                "assertions": [
                    {"type": "status_code", "expected": 201 if method == "POST" else 200},
                    {"type": "json_contains", "field": "success", "value": True},
                ],
                "dependencies": [],
            })

        # ② 边界场景 (有必填字段时才生成)
        if required_fields and method in ("POST", "PUT"):
            cases.append({
                "name": f"边界值: {name}",
                "type": "boundary",
                "description": f"边界值测试: {name} — 超长/特殊字符",
                "test_data": {
                    "params": {},
                    "headers": {"Content-Type": "application/json"},
                    "body": {f: "A" * 256 for f in required_fields},
                },
                "assertions": [
                    {"type": "status_code", "expected": 400},
                ],
                "dependencies": [],
            })

        # ③ 异常场景 (有非必填字段时: 只传必填, 缺非必填)
        non_required = [f for f in properties if f not in required_fields]
        if non_required and method in ("POST", "PUT"):
            cases.append({
                "name": f"异常场景(缺参数): {name}",
                "type": "exception",
                "description": f"异常测试: {name} — 缺少必填参数",
                "test_data": {
                    "params": {},
                    "headers": {"Content-Type": "application/json"},
                    "body": {},  # 空body
                },
                "assertions": [
                    {"type": "status_code", "expected": 400},
                ],
                "dependencies": [],
            })

        # ④ 依赖场景 — 找当前接口依赖了哪些上游接口
        call_deps = dependencies.get("call_dependencies", [])
        iface_deps = [d for d in call_deps if d.get("source") == name]
        if iface_deps:
            dep_names = [d["target"] for d in iface_deps]
            cases.append({
                "name": f"依赖场景: {name}",
                "type": "dependency",
                "description": f"先执行 {', '.join(dep_names)} → 再调用 {name}",
                "test_data": {
                    "params": {},
                    "headers": {
                        "Content-Type": "application/json",
                        "Authorization": "Bearer ${{token}}"  # 从前置接口提取
                    },
                    "body": _build_body(fill_all=True),
                },
                "assertions": [
                    {"type": "status_code", "expected": 201 if method == "POST" else 200},
                ],
                "dependencies": dep_names,
            })

    return cases


# ══════════════════════════════════════════════════════════════
# 核心: MultiAgentOrchestrator — LangGraph StateGraph 编排
# 对应 agent_service.py L248-313
#
# 用 LangGraph 把三个Agent串成流水线:
#   parser → dependency_analyzer → testcase_generator → END
#
# 为什么用LangGraph而不是顺序函数调用?
#   - 声明式: 图结构一目了然, 添加/移除节点不改变调用逻辑
#   - 可扩展: 将来加条件边/循环/检查点只需改图定义
#   - 可观测: 节点间State自动传递, 天然支持执行追踪
# ══════════════════════════════════════════════════════════════

def run_agent_workflow(
    task_description: str,
    rag_index: Dict[str, Any],
    interfaces: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Agent编排工作流 — 3-Agent协作

    Workflow (from agent_service.py L259-274):
      InterfaceParserAgent → DependencyAnalyzerAgent → TestCaseGeneratorAgent

    架构: "外层Workflow编排 + 内层Agent智能决策"
      - Workflow层: StateGraph线性编排（add_edge无条件边）
      - Agent层: 每个节点内LLM自主决策

    Args:
        task_description: 自然语言任务 ("为支付模块生成测试用例, 含完整CRUD流程")
        rag_index: RAG知识库索引 (from demo_07/build_index.py → step_07_rag_index.json)
        interfaces: 全部接口列表 (from step_02_interfaces.json)
    Returns:
        {parsed_interfaces, dependencies, test_cases, workflow_log, ...}
    """
    # ── 初始化AgentState ──
    state = AgentState(task=task_description)
    state.context = {"workflow_log": []}

    print(f"\n  [Agent] 任务: {task_description}")
    print(f"  [Agent] 可用接口: {len(interfaces)} 个")
    print(f"  [Agent] RAG索引: {'已加载' if rag_index else '无'}")

    orchestration_mode = "sequential"  # 默认

    # ── 路径1: LangGraph StateGraph (主路径) ──
    try:
        from langgraph.graph import StateGraph, END

        # 构建StateGraph — 用dict作为State载体(兼容性更好)
        workflow = StateGraph(dict)

        # 添加三个节点 — 每个是一个lambda包装器
        # 包装器的职责: dict ↔ AgentState 转换 + 调用实际Agent函数
        def _parser_node(state_dict: dict) -> dict:
            st = AgentState.from_dict(state_dict, task_description)
            _run_parser_agent(st, interfaces, rag_index)
            return st.to_dict()

        def _dep_node(state_dict: dict) -> dict:
            st = AgentState.from_dict(state_dict, task_description)
            _run_dependency_agent(st)
            return st.to_dict()

        def _gen_node(state_dict: dict) -> dict:
            st = AgentState.from_dict(state_dict, task_description)
            _run_generator_agent(st, rag_index)
            return st.to_dict()

        workflow.add_node("parser", _parser_node)
        workflow.add_node("dependency_analyzer", _dep_node)
        workflow.add_node("testcase_generator", _gen_node)

        workflow.set_entry_point("parser")
        workflow.add_conditional_edges(
            "parser",
            _should_continue_after_parser,
            {"continue": "dependency_analyzer", "end": END},
        )
        workflow.add_conditional_edges(
            "dependency_analyzer",
            _should_continue_after_analysis,
            {"continue": "testcase_generator", "end": END},
        )
        workflow.add_edge("testcase_generator", END)

        compiled = workflow.compile()

        # 执行工作流
        initial = {
            "current_task": task_description,
            "parsed_interfaces": [],
            "dependencies": {},
            "test_cases": [],
            "context": state.context,
        }
        final_state = compiled.invoke(initial)

        # 从final_state提取结果回填到state
        state.parsed_interfaces = final_state.get("parsed_interfaces", [])
        state.dependencies = final_state.get("dependencies", {})
        state.test_cases = final_state.get("test_cases", [])
        orchestration_mode = "langgraph"

        print(f"  [Agent] 编排模式: LangGraph StateGraph")

    except ImportError:
        print(f"  [Agent] LangGraph未安装, 使用顺序调用模式")
        # 顺序函数调用 (功能完全等价)
        _run_parser_agent(state, interfaces, rag_index)
        _run_dependency_agent(state)
        _run_generator_agent(state, rag_index)
    except Exception as e:
        print(f"  [WARN] LangGraph执行失败: {e}, 回退到顺序调用")
        state = AgentState(task=task_description)
        state.context = {"workflow_log": []}
        _run_parser_agent(state, interfaces, rag_index)
        _run_dependency_agent(state)
        _run_generator_agent(state, rag_index)
        orchestration_mode = "sequential_fallback"

    # ── 打印流水线摘要 ──
    workflow_log = state.context.get("workflow_log", [])
    for entry in workflow_log:
        agent = entry.get("agent", "Unknown")
        mode = entry.get("mode", "?")
        extra = {k: v for k, v in entry.items() if k not in ("agent", "mode")}
        print(f"    [{agent}] {mode}: {extra}")

    print(f"\n  [Agent] 解析接口: {len(state.parsed_interfaces)} 个")
    print(f"  [Agent] 依赖关系: {state.dependencies.get('total_dependencies', 0)} 条")
    print(f"  [Agent] 生成用例: {len(state.test_cases)} 条")

    # ── 组装返回结果 ──
    # 构建agent_steps (供run_agent.py展示)
    agent_steps = []
    for entry in workflow_log:
        agent_steps.append({
            "agent": entry.get("agent", "Unknown"),
            "action": entry.get("mode", "?"),
            "result": json.dumps(
                {k: v for k, v in entry.items() if k not in ("agent", "mode")},
                ensure_ascii=False
            ),
        })

    return {
        # 核心产出
        "parsed_interfaces": state.parsed_interfaces,
        "dependencies": state.dependencies,
        "test_cases": state.test_cases,
        # 元信息
        "task_description": task_description,
        "orchestration_mode": orchestration_mode,
        "workflow_log": workflow_log,
        "agent_steps": agent_steps,
        "agent_count": 3,
        # 向后兼容 (run_agent.py使用的字段)
        "agent_cases": state.test_cases,
        "additional_cases": state.test_cases,
        "matched_interfaces": [i.get("name", "") for i in state.parsed_interfaces],
        "total_additional_cases": len(state.test_cases),
        "relevant_rag_docs": [],
        # 摘要信息
        "interface_names": [i.get("name", "") for i in state.parsed_interfaces],
        "execution_order": state.dependencies.get("execution_order", []),
        "groups": state.dependencies.get("groups", {}),
    }


# ══════════════════════════════════════════════════════════════
# AgentStateDict TypedDict — 类型安全的 AgentState
#   (backend/agent_service.py L12-20 同款)
#
#   AgentState 类提供了运行时的灵活性（方法、默认值），
#   AgentStateDict TypedDict 提供了编译期的类型安全性。
#   两者描述同一数据结构，学员可以根据偏好选用。
# ══════════════════════════════════════════════════════════════

try:
    from typing import TypedDict

    class AgentStateDict(TypedDict, total=False):
        """Agent 共享状态的类型定义——与 AgentState 类字段一一对应"""
        messages: List[Dict[str, Any]]          # Agent 对话历史
        current_task: str                       # 用户任务描述
        parsed_interfaces: List[Dict]           # Parser 输出 → Analyzer 输入
        dependencies: Dict[str, Any]            # Analyzer 输出 → Generator 输入
        test_cases: List[Dict]                  # 最终产出
        context: Dict[str, Any]                 # RAG 上下文 + 执行日志
        # 工作流控制字段
        skip_analysis: bool                     # 条件边：无接口时跳过依赖分析
        skip_generation: bool                   # 条件边：无接口时跳过用例生成
        error_message: str                      # 错误信息传递

except ImportError:
    # typing_extensions 降级
    AgentStateDict = dict  # type: ignore


# ══════════════════════════════════════════════════════════════
# LangGraph 条件边 — 空接口时跳过后续步骤
#   (backend/agent_service.py L265-285 同款)
#
#   为什么需要条件边？当 InterfaceParserAgent 没有匹配到任何接口时，
#   后续的 DependencyAnalyzerAgent 和 TestCaseGeneratorAgent 没有意义，
#   白白消耗 LLM token 和执行时间。条件边在 parsed_interfaces 为空时
#   直接跳到 END，提前终止工作流。
#
#   条件函数签名: (state_dict: dict) → str
#     返回下一个节点名或 END
# ══════════════════════════════════════════════════════════════

def _should_continue_after_parser(state_dict: dict) -> str:
    """
    条件边: parser 节点后——检查是否有解析出的接口。
    无接口 → 直接 END；有接口 → 继续到 dependency_analyzer。
    """
    parsed = state_dict.get("parsed_interfaces", [])
    if not parsed:
        state_dict["skip_analysis"] = True
        state_dict["skip_generation"] = True
        # 记录到 context
        ctx = state_dict.get("context", {})
        ctx.setdefault("workflow_log", []).append({
            "agent": "Router",
            "decision": "skip_all",
            "reason": "Parser 未匹配到接口，跳过依赖分析和用例生成",
        })
        return "end"
    return "continue"


def _should_continue_after_analysis(state_dict: dict) -> str:
    """
    条件边: dependency_analyzer 节点后——检查依赖分析是否完成。
    有执行顺序 → 继续生成用例；无 → 跳过生成。
    """
    deps = state_dict.get("dependencies", {})
    exec_order = deps.get("execution_order", []) if isinstance(deps, dict) else []
    if not exec_order:
        ctx = state_dict.get("context", {})
        ctx.setdefault("workflow_log", []).append({
            "agent": "Router",
            "decision": "skip_generation",
            "reason": "依赖分析无执行顺序，跳过用例生成",
        })
        state_dict["skip_generation"] = True
        return "end"
    return "continue"


# ══════════════════════════════════════════════════════════════
# create_agent_subgraph() — 子图封装辅助
#   (backend/agent_service.py L290-314 同款)
#
#   为什么需要子图？当工作流变复杂时（如加入 RAG 注入、质量检查、
#   重试循环），直接在顶层图添加节点会让图结构难以理解。
#   子图将相关节点封装为一组，对外暴露为单个复合节点。
#
#   使用场景:
#     - RAG 注入子图: 检索 → 筛选 → 注入 → 三合一为 "rag_enrich" 节点
#     - 质量检查子图: 覆盖率检查 → 补充生成 → 再检查 → "quality_gate" 节点
# ══════════════════════════════════════════════════════════════

def create_agent_subgraph(
    name: str,
    node_functions: List[tuple],  # [(node_name, handler_fn), ...]
    edges: Optional[List[tuple]] = None,  # [(from_node, to_node), ...]
    entry_node: Optional[str] = None,
    conditional_edges: Optional[List[tuple]] = None,  # [(from_node, condition_fn, mapping_dict), ...]
) -> "StateGraph":
    """
    封装一组节点为可复用的子图。

    Args:
        name: 子图名称 (如 "rag_enrich", "quality_gate")
        node_functions: [(node_name, handler_fn), ...] 子图内的节点
        edges: [(from_node, to_node), ...] 子图内的边
        entry_node: 入口节点名 (不指定则取第一个)
        conditional_edges: [(from_node, condition_fn, {True: "node_a", False: "node_b"})]
                           子图内的条件边

    Returns: LangGraph StateGraph 子图对象 (可被父图 add_node 引用)

    Example:
        rag_subgraph = create_agent_subgraph(
            "rag_enrich",
            [("retrieve", retrieve_fn), ("filter", filter_fn), ("inject", inject_fn)],
            [("retrieve", "filter"), ("filter", "inject")],
        )
        parent_workflow.add_node("rag_enrich", rag_subgraph)
    """
    try:
        from langgraph.graph import StateGraph, END
    except ImportError:
        # LangGraph 不可用时返回一个简单的函数组合器
        def _sequential_runner(state_dict: dict) -> dict:
            """降级：顺序执行所有节点函数"""
            for _, handler in node_functions:
                if callable(handler):
                    result = handler(state_dict)
                    if isinstance(result, dict):
                        state_dict.update(result)
            return state_dict
        _sequential_runner.__name__ = name
        return _sequential_runner

    subgraph = StateGraph(dict)

    # 添加节点
    for node_name, handler in node_functions:
        subgraph.add_node(node_name, handler)

    # 添加入口
    if entry_node:
        subgraph.set_entry_point(entry_node)
    elif node_functions:
        subgraph.set_entry_point(node_functions[0][0])

    # 添加普通边
    if edges:
        for from_n, to_n in edges:
            if to_n == "END":
                subgraph.add_edge(from_n, END)
            else:
                subgraph.add_edge(from_n, to_n)

    # 添加条件边
    if conditional_edges:
        for from_node, condition_fn, mapping in conditional_edges:
            mapped = {}
            for k, v in mapping.items():
                mapped[k] = v if v != "END" else END
            subgraph.add_conditional_edges(from_node, condition_fn, mapped)

    # 包装为可被父图调用的节点
    compiled = subgraph.compile()

    def _subgraph_node(state_dict: dict) -> dict:
        """子图包装器——父图调用此函数即执行整个子图"""
        return compiled.invoke(state_dict)

    _subgraph_node.__name__ = name
    return _subgraph_node
