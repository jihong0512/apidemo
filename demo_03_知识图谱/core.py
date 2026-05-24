"""
demo_03 知识图谱 — 核心逻辑
═══════════════════════════════════════════════════════════════
从接口列表构建 API 知识图谱：APIInterface 节点 + DEPENDS_ON 关系。

对应课件: 第03讲 Neo4j知识图谱
源码参考:
  - backend/app/services/db_service.py (886行, L422-588 构建部分)
  - backend/app/services/dependency_analyzer.py (1044行, 依赖分析)
  - backend/app/services/relationship_analyzer.py (业务关系定义)

核心设计:
  为什么用图数据库？API 依赖天然是图——login→create→read→update→delete
  用 SQL JOIN 需要 4 层嵌套，Cypher 一行 MATCH ... -[:DEPENDS_ON*]-> 搞定。
  Neo4j 邻接表让 BFS 遍历 O(1) 跳转，MySQL 需要 O(n) 索引查找。

  为什么用 MERGE 而非 CREATE？同一接口可能被多次解析（重新上传文档），
  CREATE 会产生重复节点。MERGE 是幂等的——"有则匹配，无则创建"。

  为什么有 Neo4j 和 networkx 两条路径？学员可能没装 Neo4j。
  networkx 零外部依赖，API 完全一致，只是存储介质不同。

  Login 节点的特殊地位：它是整个依赖图的根节点。
  几乎所有需要认证的接口都依赖 login 获取 token。

  15种业务关系类型：CONTAINS, OWNS, BINDS_TO, ASSOCIATES_WITH, CREATES,
  USES, COLLECTS, GENERATES, PARTICIPATES_IN, MANAGES, HAS_ATTRIBUTE,
  HAS_VERSION, SUPPORTS, SHARES_WITH, CONNECTS_TO
═══════════════════════════════════════════════════════════════
"""
import sys
import re
import hashlib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import add_project_to_sys_path, neo4j_manager, Config, llm_client
add_project_to_sys_path(__file__)


# ══════════════════════════════════════════════════════════════
# 15种业务关系类型 (backend/relationship_analyzer.py L37-130 同款)
# ══════════════════════════════════════════════════════════════
BUSINESS_RELATIONSHIP_TYPES = {
    "CONTAINS": "包含——A包含B",
    "OWNS": "拥有——A拥有B",
    "BINDS_TO": "绑定——A绑定到B",
    "ASSOCIATES_WITH": "关联——A与B关联",
    "CREATES": "创建——A创建B",
    "USES": "使用——A使用B",
    "COLLECTS": "收藏——A收藏B",
    "GENERATES": "生成——A生成B",
    "PARTICIPATES_IN": "参与——A参与B",
    "MANAGES": "管理——A管理B",
    "HAS_ATTRIBUTE": "属性——A有属性B",
    "HAS_VERSION": "版本——A有版本B",
    "SUPPORTS": "支持——A支持B",
    "SHARES_WITH": "共享——A与B共享",
    "CONNECTS_TO": "连接——A连接到B",
}


# ══════════════════════════════════════════════════════════════
# 主入口——被 build_graph.py 调用
# ══════════════════════════════════════════════════════════════

def build_knowledge_graph(interfaces: list) -> dict:
    """
    构建 API 接口知识图谱。Neo4j 可用时写入图数据库并返回查询结果，
    不可用时降级到 networkx 内存图。

    Returns: {graph, nodes, edges, node_count, edge_count, dependency_types}
      graph: networkx.DiGraph (供 entry 序列化邻接表)
    """
    # 阶段0: 路由——Neo4j 还是 networkx
    session = neo4j_manager.get_session()
    if session is not None:
        try:
            print("  [INFO] Neo4j 图数据库可用，使用 Cypher 写入")
            result = _build_graph_core(interfaces, storage=_Neo4jStorage(session))
            session.close()
            return result
        except Exception as e:
            print(f"  [WARN] Neo4j 写入失败，降级到 networkx: {e}")
            try:
                session.close()
            except Exception:
                pass

    print("  [INFO] Neo4j 不可用，使用 networkx 本地图模式")
    return _build_graph_core(interfaces, storage=_NetworkxStorage())


# ══════════════════════════════════════════════════════════════
# 图构建核心——统一的关系推断逻辑（Neo4j 和 networkx 共用）
# ══════════════════════════════════════════════════════════════

class _GraphStorage:
    """图存储抽象基类——策略模式，Neo4j 和 networkx 各自实现"""
    def add_node(self, node_id: str, props: dict): pass
    def add_edge(self, source: str, target: str, props: dict): pass
    def has_edge(self, source: str, target: str) -> bool: return False
    def setup(self): pass


class _NetworkxStorage(_GraphStorage):
    """networkx 内存图存储"""
    def __init__(self):
        import networkx as nx
        self.G = nx.DiGraph()

    def add_node(self, node_id, props):
        self.G.add_node(node_id, **props)

    def add_edge(self, source, target, props):
        # 去重：networkx 允许多条同向边，但我们只保留第一条
        if not self.G.has_edge(source, target):
            self.G.add_edge(source, target, **props)
            return True
        return False


class _Neo4jStorage(_GraphStorage):
    """Neo4j 图数据库存储 (backend/db_service.py L422-588 同款)"""
    def __init__(self, session):
        self.session = session
        self._added_edges = set()  # 去重集合

    def setup(self):
        """清理旧数据：只删除 demo_03 标记的节点，避免误删学员项目数据"""
        try:
            self.session.run("MATCH (n:APIInterface {demo_source: 'demo_03'}) DETACH DELETE n")
        except Exception as e:
            print(f"  [WARN] 清理旧数据失败（可忽略）: {e}")

    def add_node(self, node_id, props):
        # MERGE 幂等写入：同 method+path 的接口只创建一次 (backend L484 同款)
        self.session.run("""
            MERGE (a:APIInterface {method: $method, path: $path, demo_source: $demo_source})
            SET a.node_id = $node_id, a.name = $name, a.service = $service,
                a.description = $description, a.crud_type = $crud_type,
                a.category = $category, a.version = $version,
                a.headers = $headers, a.params = $params
        """, **props)

    def add_edge(self, source, target, props):
        key = (source, target, props.get('dep_key', ''))
        if key in self._added_edges:
            return False
        self._added_edges.add(key)
        self.session.run("""
            MATCH (a:APIInterface {node_id: $s_id, demo_source: 'demo_03'})
            MATCH (b:APIInterface {node_id: $t_id, demo_source: 'demo_03'})
            MERGE (a)-[r:DEPENDS_ON {dep_key: $dep_key}]->(b)
            SET r.type = $type, r.description = $description,
                r.extract_fields = $extract_fields, r.confidence = $confidence
        """, source_id=source, target_id=target,
             s_id=source, t_id=target, **props)


def _build_graph_core(interfaces: list, storage: _GraphStorage) -> dict:
    """
    统一图构建逻辑——关系推断与 storage 解耦。
    Neo4j 和 networkx 两个路径共用这里的依赖分析逻辑，
    只是节点/边的持久化方式不同（策略模式）。

    关系推断与 backend/dependency_analyzer.py L61-120 对齐。
    """
    storage.setup()
    nodes = []
    edges = []

    # ── 阶段1: 创建 APIInterface 节点 (10个核心属性) ──
    import json as json_module
    for iface in interfaces:
        node_id = _gen_id(iface)
        node = {
            "id": node_id, "label": "APIInterface",
            "name": iface["name"], "method": iface["method"],
            "path": iface.get("path", iface.get("url", "")),
            "service": iface.get("service", "unknown"),
            "description": iface.get("description", ""),
            "crud_type": iface.get("crud_type", "READ"),
            "category": iface.get("category", "general"),
            "version": iface.get("version", "v1"),
            "requires_auth": _check_auth_required(iface.get("headers", {})),
        }
        nodes.append(node)

        # Neo4j 额外需要 JSON 序列化的 headers/params 字段
        neo4j_props = {
            "node_id": node_id, "name": node["name"], "method": node["method"],
            "path": node["path"], "service": node["service"],
            "description": node["description"], "crud_type": node["crud_type"],
            "category": node["category"], "version": node["version"],
            "headers": json_module.dumps(iface.get("headers", {}), ensure_ascii=False),
            "params": json_module.dumps(iface.get("params", {}), ensure_ascii=False),
            "demo_source": "demo_03",
        }
        storage.add_node(node_id, neo4j_props if isinstance(storage, _Neo4jStorage) else node)

    # ── 阶段2: 查找 login 根节点 ──
    login_nodes = _find_login_nodes(interfaces, nodes)
    print(f"  [INFO] 检测到 {len(login_nodes)} 个认证节点: {[n['name'] for n in login_nodes]}")

    # ── 阶段3: 构建 DEPENDS_ON 关系 ──

    # 3a: data_flow——token 依赖（有 Authorization header → login）
    for node in nodes:
        if not node.get("requires_auth"):
            continue
        for ln in login_nodes:
            if node["id"] == ln["id"]:
                continue
            _record_edge(storage, edges, node["id"], ln["id"],
                         "data_flow", f"{node['name']} 需要登录 token",
                         ["token"], 0.95, dep_key="token")

    # 3b: data_flow——ID 依赖（含 {xxx} 路径参数 且非 POST → CREATE）
    for node in nodes:
        path_params = _extract_path_params(node.get("path", ""))
        if not path_params or node.get("method") == "POST":
            continue
        create_nodes = [
            n for n in nodes
            if n.get("crud_type") == "CREATE"
            and n.get("category") == node.get("category")
            and n["id"] != node["id"]
        ]
        for cn in create_nodes:
            _record_edge(storage, edges, node["id"], cn["id"],
                         "data_flow", f"{node['name']} 先创建资源获取 {path_params[0]}",
                         path_params, 0.9, dep_key=path_params[0])

    # 3c: business_logic——CRUD 顺序链 CREATE→READ→UPDATE→DELETE
    #   backend/dependency_analyzer.py L292-350 的 CRUD 依赖链同款
    for category in set(n.get("category") for n in nodes):
        cat_nodes = [n for n in nodes if n.get("category") == category]
        creates = [n for n in cat_nodes if n.get("crud_type") == "CREATE"]
        reads = [n for n in cat_nodes if n.get("crud_type") == "READ"]
        updates = [n for n in cat_nodes if n.get("crud_type") == "UPDATE"]
        deletes = [n for n in cat_nodes if n.get("crud_type") == "DELETE"]

        for src_group, tgt_group, order in [(creates, reads, 1), (reads, updates, 2), (updates, deletes, 3)]:
            for src in src_group:
                for tgt in tgt_group:
                    if src["id"] == tgt["id"]:
                        continue
                    _record_edge(storage, edges, src["id"], tgt["id"],
                                 "business_logic",
                                 f"CRUD顺序: {src['name']} → {tgt['name']}",
                                 [], 0.85, order=order, dep_key=f"crud_{order}")

    dep_counts = {}
    for e in edges:
        key = e.get("type", "unknown")
        dep_counts[key] = dep_counts.get(key, 0) + 1

    # 如果 storage 没有 .G 属性（Neo4jStorage），创建一个 networkx 给 entry 序列化用
    if not hasattr(storage, 'G'):
        storage.G = _make_nx_from_edges(nodes, edges)

    return {
        "graph": storage.G,
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "dependency_types": dep_counts,
    }


def _record_edge(storage, edges_list, source, target, etype, desc, extract_fields, confidence, **extra):
    """记录一条边（去重 + 持久化）"""
    edge = {
        "source": source, "target": target,
        "type": etype, "description": desc,
        "extract_fields": extract_fields, "confidence": confidence,
    }
    edge.update(extra)
    if storage.add_edge(source, target, edge):
        edges_list.append(edge)


def _make_nx_from_edges(nodes: list, edges: list):
    """从节点和边列表构造 networkx.DiGraph（用于 Neo4j 路径的邻接表序列化）"""
    import networkx as nx
    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["id"], **n)
    for e in edges:
        if not G.has_edge(e["source"], e["target"]):
            G.add_edge(e["source"], e["target"], **e)
    return G


# ══════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════

def _gen_id(iface: dict) -> str:
    """生成接口唯一ID——MD5(method|path|name) 前12位。
    为什么用 MD5 而非自增？确定性：同 method+path 总是同 ID，跨 demo 可引用。"""
    raw = f"{iface.get('method','GET')}|{iface.get('path',iface.get('url',''))}|{iface.get('name','')}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()[:12]


def _check_auth_required(headers: dict) -> bool:
    """检查是否需要认证——headers 中是否有 Authorization/token 关键字。
    backend/dependency_analyzer.py L22-35 同款检测逻辑。"""
    if not headers:
        return False
    s = str(headers).lower()
    return any(kw in s for kw in ('authorization', 'token', 'bearer', 'auth', 'jwt'))


def _find_login_nodes(interfaces: list, nodes: list) -> list:
    """查找所有 login/认证 接口节点。
    可能多个（手机登录/密码登录/OAuth），都视为 token 来源。"""
    login_kw = ['login', '登录', 'auth', 'signin', 'sign_in']
    result = []
    for node, iface in zip(nodes, interfaces):
        nl = node['name'].lower()
        pl = node.get('path', '').lower()
        if any(kw in nl for kw in login_kw) or \
           (any(kw in pl for kw in login_kw) and node.get('method') == 'POST'):
            result.append(node)
    return result


def _extract_path_params(path: str) -> list:
    """提取路径参数: {device_id} → ['device_id'], :device_id → ['device_id']"""
    if not path:
        return []
    return re.findall(r'\{(\w+)\}', path) + re.findall(r':(\w+)(?:/|$)', path)


# ══════════════════════════════════════════════════════════════
# 15种业务关系的 Cypher 查询示例 (教学参考，不自动执行)
# ══════════════════════════════════════════════════════════════

CYPHEER_QUERY_EXAMPLES = {
    "CONTAINS": """
-- 查询某服务下包含的所有接口
MATCH (svc:Service {name: 'device-service'})-[:CONTAINS]->(api:APIInterface)
RETURN api.name, api.method, api.path, api.crud_type
ORDER BY api.crud_type
""",

    "OWNS": """
-- 查询某用户拥有的所有项目
MATCH (u:User {role: 'admin'})-[:OWNS]->(p:Project)
RETURN p.name, p.created_at
ORDER BY p.created_at DESC
""",

    "BINDS_TO": """
-- 查询某个设备绑定到哪个用户
MATCH (d:Device {device_id: 'X001'})-[:BINDS_TO]->(u:User)
RETURN u.name, u.email
""",

    "ASSOCIATES_WITH": """
-- 查询与某接口关联的所有业务组
MATCH (api:APIInterface {path: '/api/v1/device/list'})-[:ASSOCIATES_WITH]->(bg:BusinessGroup)
RETURN bg.name, bg.category
""",

    "CREATES": """
-- 查询某 POST 接口创建了哪些资源
MATCH (api:APIInterface {crud_type: 'CREATE'})-[:CREATES]->(res:Resource)
RETURN api.path, res.type, res.id_pattern
""",

    "USES": """
-- 查询哪些接口使用了某个 token
MATCH (api:APIInterface)-[r:DEPENDS_ON {dep_key: 'token'}]->(login:APIInterface)
RETURN api.name, api.path, r.confidence
ORDER BY r.confidence DESC
""",

    "COLLECTS": """
-- 查询某用户收藏的接口列表
MATCH (u:User {name: '张三'})-[:COLLECTS]->(api:APIInterface)
RETURN api.name, api.method, api.path
""",

    "GENERATES": """
-- 查询某接口生成的数据类型
MATCH (api:APIInterface {name: 'createDevice'})-[:GENERATES]->(data:DataSchema)
RETURN data.field_name, data.field_type, data.is_required
""",

    "PARTICIPATES_IN": """
-- 查询参与某场景的所有接口（2跳依赖链）
MATCH (s:Scenario {name: '设备全生命周期'})-[:PARTICIPATES_IN]->(api:APIInterface)
MATCH (api)-[:DEPENDS_ON*0..2]->(dep:APIInterface)
RETURN DISTINCT api.name, collect(DISTINCT dep.name) AS dependencies
""",

    "MANAGES": """
-- 查询管理员管理的所有服务
MATCH (admin:User {role: 'admin'})-[:MANAGES]->(svc:Service)
OPTIONAL MATCH (svc)-[:CONTAINS]->(api:APIInterface)
RETURN svc.name, count(api) AS api_count
ORDER BY api_count DESC
""",

    "HAS_ATTRIBUTE": """
-- 查询某接口的请求参数属性
MATCH (api:APIInterface {path: '/api/v1/device/create'})-[:HAS_ATTRIBUTE]->(param:Parameter)
RETURN param.name, param.type, param.required, param.description
""",

    "HAS_VERSION": """
-- 查询某接口的所有历史版本
MATCH (api:APIInterface {name: 'login'})-[:HAS_VERSION]->(ver:APIVersion)
RETURN ver.version, ver.changed_at, ver.change_log
ORDER BY ver.changed_at DESC
""",

    "SUPPORTS": """
-- 查询支持某 Content-Type 的接口
MATCH (api:APIInterface)-[:SUPPORTS]->(ct:ContentType {value: 'application/json'})
RETURN api.path, api.method
""",

    "SHARES_WITH": """
-- 查询跨服务共享的数据模型
MATCH (svc1:Service)-[:SHARES_WITH]->(model:DataModel)<-[:SHARES_WITH]-(svc2:Service)
WHERE svc1.name < svc2.name
RETURN svc1.name AS service_a, svc2.name AS service_b, model.name
""",

    "CONNECTS_TO": """
-- 查询某接口直接连接的上下游（1跳依赖视图）
MATCH (api:APIInterface {name: 'getDeviceDetail'})
MATCH (api)-[r:DEPENDS_ON]->(downstream:APIInterface)
OPTIONAL MATCH (upstream:APIInterface)-[:DEPENDS_ON]->(api)
RETURN api.name AS current,
       collect(DISTINCT upstream.name) AS depends_on_me,
       collect(DISTINCT downstream.name) AS i_depend_on
""",
}

# ── 补充：3 个实战分析查询（图算法类） ──

ANALYTICAL_CYPHEER_EXAMPLES = {
    "最长依赖链": """
-- 找出图中最长的 DEPENDS_ON 链路（用于评估接口耦合度）
MATCH path = (start:APIInterface {demo_source: 'demo_03'})
             -[:DEPENDS_ON*1..6]->(end:APIInterface {demo_source: 'demo_03'})
RETURN start.name, end.name, length(path) AS chain_length
ORDER BY chain_length DESC
LIMIT 10
""",

    "孤立节点检测": """
-- 找出没有任何依赖关系的接口（可能是文档解析遗漏）
MATCH (api:APIInterface {demo_source: 'demo_03'})
WHERE NOT (api)-[:DEPENDS_ON]-()
RETURN api.name, api.method, api.path, api.service
""",

    "关键路径分析": """
-- 找出被依赖次数最多的 top-5 接口（改动影响面最大的节点）
MATCH (api:APIInterface {demo_source: 'demo_03'})<-[r:DEPENDS_ON]-()
RETURN api.name, api.service, count(r) AS dependents
ORDER BY dependents DESC
LIMIT 5
""",
}


# ══════════════════════════════════════════════════════════════
# LangChain GraphCypherQAChain 集成 (可选路径)
# ══════════════════════════════════════════════════════════════

def query_graph_natural_language(question: str, session=None) -> dict:
    """
    自然语言查询知识图谱——用中文提问，自动翻译为 Cypher 并执行。

    为什么需要这个？学员不会写 Cypher，但需要查询"login 依赖哪些接口？"。
    GraphCypherQAChain 用 LLM 把中文翻译成 Cypher，零门槛查询。

    两条路径:
      1. LangChain GraphCypherQAChain → LLM 翻译 → Neo4j 执行（主路径）
      2. 关键词匹配 + 预置查询模板 → Neo4j 直接执行（降级路径）

    降级路径的关键词覆盖：依赖链/CRUD/认证/孤立节点/分组/最长链
    """
    # 获取 Neo4j session
    should_close = False
    if session is None:
        try:
            session = neo4j_manager.get_session()
            should_close = True
        except Exception:
            pass

    if session is None:
        return {"error": "Neo4j 不可用，无法执行自然语言查询", "results": [], "cypher": ""}

    try:
        # ── 路径1: LangChain GraphCypherQAChain ──
        try:
            from langchain_community.chains.graph_qa.cypher import GraphCypherQAChain
            from langchain_community.graphs import Neo4jGraph

            graph = Neo4jGraph(
                url=Config.NEO4J_URI,
                username=Config.NEO4J_USER,
                password=Config.NEO4J_PASSWORD,
            )
            chain = GraphCypherQAChain.from_llm(
                llm=llm_client.get_langchain_llm() if hasattr(llm_client, 'get_langchain_llm') else None,
                graph=graph,
                verbose=False,
                validate_cypher=True,
                top_k=10,
            )
            result = chain.invoke({"query": question})
            return {
                "answer": result.get("result", ""),
                "cypher": result.get("intermediate_steps", [{}])[-1].get("query", "") if result.get("intermediate_steps") else "",
                "results": [],
            }
        except ImportError:
            pass  # 降级到模板匹配
        except Exception as e:
            print(f"  [WARN] LangChain 查询失败，降级到模板匹配: {e}")

        # ── 路径2: 关键词匹配 + 预置查询模板 ──
        q_lower = question.lower()

        if any(kw in q_lower for kw in ('依赖', 'depend', '依赖链', '依赖关系')):
            cypher = """
                MATCH (a:APIInterface {demo_source: 'demo_03'})-[r:DEPENDS_ON]->(b:APIInterface {demo_source: 'demo_03'})
                RETURN a.name AS source, b.name AS target, r.type AS dep_type, r.description AS reason
                LIMIT 30
            """
        elif any(kw in q_lower for kw in ('crud', '增删改查', '顺序')):
            cypher = """
                MATCH (a:APIInterface {demo_source: 'demo_03'})
                RETURN a.crud_type AS crud_type, collect(a.name) AS apis, count(a) AS cnt
                ORDER BY cnt DESC
            """
        elif any(kw in q_lower for kw in ('login', '登录', '认证', 'token', 'auth')):
            cypher = """
                MATCH (login:APIInterface {demo_source: 'demo_03'})
                WHERE login.name =~ '.*(?i)(login|auth|signin).*'
                MATCH (api:APIInterface {demo_source: 'demo_03'})-[r:DEPENDS_ON]->(login)
                RETURN login.name AS auth_node, collect(api.name) AS dependents, count(api) AS dep_count
            """
        elif any(kw in q_lower for kw in ('孤立', '无依赖', 'orphan')):
            cypher = """
                MATCH (api:APIInterface {demo_source: 'demo_03'})
                WHERE NOT (api)-[:DEPENDS_ON]-()
                RETURN api.name, api.method, api.path, api.service
            """
        elif any(kw in q_lower for kw in ('分组', 'category', '分类', '服务', 'service')):
            cypher = """
                MATCH (api:APIInterface {demo_source: 'demo_03'})
                RETURN api.service AS service, api.category AS category, collect(api.name) AS apis, count(api) AS total
                ORDER BY total DESC
            """
        elif any(kw in q_lower for kw in ('最长', '链', '深度')):
            cypher = """
                MATCH path = (start:APIInterface {demo_source: 'demo_03'})
                             -[:DEPENDS_ON*1..6]->(end:APIInterface {demo_source: 'demo_03'})
                RETURN start.name, end.name, length(path) AS chain_length
                ORDER BY chain_length DESC
                LIMIT 10
            """
        else:
            # 默认：列出所有接口及其关系
            cypher = """
                MATCH (a:APIInterface {demo_source: 'demo_03'})
                OPTIONAL MATCH (a)-[r:DEPENDS_ON]->(b:APIInterface {demo_source: 'demo_03'})
                RETURN a.name, a.method, a.path, a.crud_type,
                       collect({target: b.name, type: r.type}) AS dependencies
                LIMIT 50
            """

        result = session.run(cypher)
        records = [dict(r) for r in result]
        return {
            "answer": f"查询返回 {len(records)} 条结果",
            "cypher": cypher.strip(),
            "results": records,
        }

    except Exception as e:
        return {"error": str(e), "results": [], "cypher": ""}
    finally:
        if should_close and session:
            try:
                session.close()
            except Exception:
                pass
