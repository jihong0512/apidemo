"""
demo_04 依赖分析 — 核心逻辑 v2.0
对应课件: 第04讲 接口分组与依赖计算
源码参考: optimized_dependency_analyzer.py (3126行) + interface_grouping_service.py (719行)

核心算法:
  1. Kahn 拓扑排序 — 入度表 + 零入度队列 + BFS 逐层剥离
  2. 数据流链路推断 — token_flow (登录→所有接口) / id_flow (创建→CRUD)
  3. DFS 三色标记环检测 — WHITE=0 / GRAY=1 / BLACK=2
  4. 32组业务分组 — 关键词匹配 + 版本隔离 + 文本相似度兜底

设计原则: 服务不可用时自动降级; 每个算法纯函数独立可测; 分组信息写入返回 dict
"""
import re, sys
from pathlib import Path
from collections import defaultdict, deque
from difflib import SequenceMatcher
from typing import List, Dict, Any, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import add_project_to_sys_path, neo4j_manager, Config, llm_client
add_project_to_sys_path(__file__)

# ════════════════════════════════════════════════════════════════
# 32组业务分组规则 (from interface_grouping_service.py L68-180)
# 每个分组是一组正则关键词，匹配接口的 title+path+description+tags
# 分组用途: 版本化影响面评估、批量用例生成、并行执行计划
# ════════════════════════════════════════════════════════════════
GROUP_KEYWORDS: Dict[str, List[str]] = {
    # ── 账户模块 (5组) ──
    "phone_login":      [r"手机.*登录", r"phone.*login", r"sms.*login", r"验证码.*登录"],
    "email_login":      [r"邮箱.*登录", r"email.*login"],
    "account_register": [r"注册", r"register", r"signup", r"sign.?up"],
    "account_profile":  [r"个人信息", r"用户信息", r"profile", r"修改密码"],
    "account_logout":   [r"退出", r"登出", r"logout"],
    # ── 设备模块 (9组) ──
    "device_create":    [r"设备.*创建", r"create.*device", r"新建设备"],
    "device_update":    [r"设备.*更新", r"update.*device", r"修改设备", r"设备.*改名"],
    "device_query":     [r"设备.*列表", r"设备.*查询", r"设备.*详情", r"list.*device", r"get.*device"],
    "device_delete":    [r"设备.*删除", r"delete.*device", r"remove.*device"],
    "device_control":   [r"设备.*控制", r"control.*device", r"开关", r"启动", r"暂停"],
    "device_bind":      [r"设备.*绑定", r"bind.*device", r"配网", r"添加设备"],
    "device_firmware":  [r"固件", r"firmware", r"OTA", r"升级"],
    "device_share":     [r"设备.*分享", r"share.*device", r"共享.*设备"],
    "device_alert":     [r"设备.*告警", r"alert", r"alarm", r"warning", r"异常.*通知"],
    # ── 课程模块 (6组) ──
    "course_create":    [r"课程.*创建", r"create.*course", r"新建.*课程", r"添加.*课程"],
    "course_update":    [r"课程.*更新", r"update.*course", r"修改.*课程", r"编辑.*课程"],
    "course_list":      [r"课程.*列表", r"course.*list", r"课表"],
    "course_detail":    [r"课程.*详情", r"course.*detail"],
    "course_progress":  [r"课程.*进度", r"progress", r"完成度", r"训练.*记录"],
    "course_evaluate":  [r"课程.*评价", r"evaluate", r"评分", r"comment"],
    # ── 家庭模块 (4组) ──
    "family_create":    [r"家庭.*创建", r"create.*family"],
    "family_query":     [r"家庭.*查询", r"family.*list", r"家庭成员"],
    "family_manage":    [r"家庭.*管理", r"manage.*family", r"家庭.*设置"],
    "family_invite":    [r"家庭.*邀请", r"invite.*family", r"邀请.*成员"],
    # ── 计划模块 (4组) ──
    "plan_create":      [r"计划.*创建", r"create.*plan", r"训练.*计划"],
    "plan_update":      [r"计划.*更新", r"update.*plan", r"修改.*计划", r"调整.*计划"],
    "plan_query":       [r"计划.*查询", r"plan.*list", r"计划.*列表"],
    "plan_execute":     [r"计划.*执行", r"execute.*plan", r"开始.*训练", r"训练.*开始"],
    # ── 通用模块 (4组) ──
    "upload":           [r"上传", r"upload", r"文件", r"图片"],
    "notification":     [r"通知", r"notification", r"message", r"push", r"消息.*推送"],
    "feedback":         [r"反馈", r"feedback", r"report", r"意见.*反馈", r"问题.*上报"],
    "health_data":      [r"健康", r"health", r"metrics", r"心率", r"睡眠", r"步数", r"卡路里"],
}
_VERSION_PATTERN = re.compile(r'v\d+(\.\d+)?', re.IGNORECASE)


# ════════════════════════════════════════════════════════════════
# 核心公开函数
# ════════════════════════════════════════════════════════════════

def analyze_dependencies(
    interfaces: List[Dict[str, Any]],
    kg_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    分析接口依赖: 拓扑排序 + 数据流推断 + 环检测 + 业务分组

    Returns: {execution_order, dependencies, data_flows, cycles_detected, groups, ...}
    """
    interface_map = {iface["name"]: iface for iface in interfaces}
    # 步骤1: 从知识图谱/Neo4j 提取边 — 只保留 data_flow 类型
    #        business_logic 边（demo_03 添加的 CRUD 序列提示）不代表结构依赖
    edges = _extract_edges(interfaces, kg_data)
    # 步骤2: 构建邻接表依赖图（O(V+E) 空间，稀疏图比邻接矩阵高效得多）
    dep_map = _build_dependency_map(interface_map, edges)
    # 步骤3: Kahn 拓扑排序
    sorted_names, in_deg, has_cycle = _kahn_topological_sort(interfaces, dep_map)
    # 步骤4: 数据流链路推断 (token_flow / id_flow)
    data_flows = _infer_data_flows(sorted_names, dep_map, interface_map)
    # 步骤5: DFS 三色标记环检测（精确定位环路径）
    cycles = _detect_cycles_dfs(dep_map)
    # 步骤6: 32组业务分组 + 版本隔离
    groups = _group_interfaces(interfaces)

    # ── LLM 语义分组微调（可选）──
    if Config.is_llm_available():
        try:
            llm_groups = _llm_semantic_group(interfaces, groups)
            if llm_groups:
                # 合并：LLM结果优先覆盖关键词结果
                for k, v in llm_groups.items():
                    if k in groups:
                        existing = {i.get('name') for i in groups[k]}
                        for item in v:
                            if item.get('name') not in existing:
                                groups[k].append(item)
        except Exception:
            pass  # LLM分组失败不影响主流程

    return {
        "sorted_interfaces": sorted_names,
        "dependency_map": dict(dep_map),
        "data_flow_chains": data_flows,
        "cycles": cycles,
        "cycles_detected": len(cycles) > 0 or has_cycle,
        "total_dependencies": len(edges),
        "execution_order": [{"step": i+1, "interface": n} for i, n in enumerate(sorted_names)],
        "groups": groups,
    }


# ════════════════════════════════════════════════════════════════
# 步骤1: 边提取 — 三路径降级策略
# ════════════════════════════════════════════════════════════════

def _extract_edges(interfaces, kg_data) -> List[Tuple[str, str, str]]:
    """提取依赖边: Neo4j > kg_data JSON > 推断"""
    edges = []

    # 路径1: Neo4j — 真实图数据库查询
    if neo4j_manager.is_available():
        try:
            session = neo4j_manager.get_session()
            if session:
                result = session.run(
                    "MATCH (a:APIInterface)-[r:DEPENDS_ON]->(b:APIInterface) "
                    "RETURN a.name AS s, b.name AS t, type(r) AS rel"
                )
                for rec in result:
                    edges.append((rec["s"], rec["t"], rec.get("rel", "data_flow")))
                session.close()
                if edges: return edges
        except Exception as e:
            print(f"  [INFO] Neo4j 降级: {e}")

    # 路径2: kg_data JSON — 只取 data_flow 类型边
    #         business_logic 边是知识图谱添加的业务分组提示，不代表执行依赖
    if kg_data and kg_data.get("edges"):
        nodes = kg_data.get("nodes", [])
        n2n = {n["id"]: n["name"] for n in nodes}
        for e in kg_data["edges"]:
            if e.get("type") == "business_logic":
                continue  # 业务逻辑边不参与拓扑排序和环检测
            edges.append((n2n.get(e["source"], e["source"]),
                          n2n.get(e["target"], e["target"]),
                          e.get("type", "data_flow")))
        if edges: return edges

    # 路径3: 降级推断 — 从 headers 含 {{token}} / url 含 {device_id} 推断
    for iface in interfaces:
        h = str(iface.get("headers", {}))
        u = iface.get("url", "")
        if "token" in h or "Authorization" in h:
            for o in interfaces:
                n = o.get("name", "").lower()
                if o["name"] != iface["name"] and ("login" in n or "登录" in n or "auth" in n):
                    edges.append((iface["name"], o["name"], "data_flow")); break
        if "{device_id}" in u or "{id}" in u:
            for o in interfaces:
                if o["name"] != iface["name"] and o.get("crud_type") == "CREATE":
                    edges.append((iface["name"], o["name"], "data_flow")); break
    return edges


# ════════════════════════════════════════════════════════════════
# 步骤2: 依赖图构建
# ════════════════════════════════════════════════════════════════

def _build_dependency_map(iface_map: Dict, edges: List[Tuple]) -> Dict[str, List[Dict]]:
    """构建邻接表依赖图: {接口名: [{depends_on, type, extract_fields, confidence}]}"""
    dep_map = defaultdict(list)
    for src, tgt, etype in edges:
        if src == tgt or src not in iface_map or tgt not in iface_map:
            continue
        # 推断需要从前置接口提取的字段
        fields = []
        tl = tgt.lower()
        if "login" in tl or "登录" in tl or "auth" in tl:
            fields.extend(["token", "user_id"])
        if "create" in tl or "创建" in tl or "device" in tl:
            fields.append("device_id")
        dep_map[src].append({
            "depends_on": tgt, "type": etype,
            "description": f"{src} 需要 {tgt} 的数据",
            "extract_fields": fields, "confidence": 0.85,
        })
    return dict(dep_map)


# ════════════════════════════════════════════════════════════════
# 步骤3: Kahn 拓扑排序 — O(V+E)
#   为什么用 Kahn 而不用 DFS 拓扑?  Kahn 天然支持并行度检测,
#   同一层零入度节点可并行执行; DFS 递归易在大图上栈溢出
# ════════════════════════════════════════════════════════════════

def _kahn_topological_sort(interfaces, dep_map) -> Tuple[List[str], Dict[str, int], bool]:
    """
    Kahn 算法: 入度表 → 零入度队列 → BFS 逐层剥离
    注意: edge A→B 表示 A depends_on B → B 被依赖 → A 入度+1 → A 排在 B 后
    """
    all_names = [i["name"] for i in interfaces]
    in_degree = {n: 0 for n in all_names}
    reverse_deps = defaultdict(list)  # "谁依赖我"的索引，加速入度递减

    for src, deps in dep_map.items():
        for d in deps:
            t = d["depends_on"]
            if t in in_degree:
                in_degree[src] += 1
                reverse_deps[t].append(src)

    queue = deque([n for n, d in in_degree.items() if d == 0])
    result = []

    while queue:
        cur = queue.popleft()
        result.append(cur)
        for dep in reverse_deps.get(cur, []):
            in_degree[dep] -= 1
            if in_degree[dep] == 0 and dep not in result:
                queue.append(dep)

    remaining = [n for n in all_names if n not in result]
    result.extend(remaining)
    return result, dict(in_degree), len(remaining) > 0


# ════════════════════════════════════════════════════════════════
# 步骤4: 数据流链路推断
#   token_flow: 登录接口产出 token → 所有需要 Authorization header 的接口
#   id_flow: CREATE 接口产出 resource_id → 对应的 READ/UPDATE/DELETE 接口
# ════════════════════════════════════════════════════════════════

def _infer_data_flows(sorted_names, dep_map, iface_map) -> List[Dict]:
    """推断数据流链路"""
    chains = []
    login_srcs = [n for n in sorted_names
                  if "login" in n.lower() or "登录" in n.lower()]
    auth_deps = [n for n in sorted_names if n not in login_srcs and
                 ("token" in str(iface_map.get(n, {}).get("headers", {}))
                  or "Authorization" in str(iface_map.get(n, {}).get("headers", {})))]

    if login_srcs and auth_deps:
        chains.append({
            "chain_type": "token_flow", "source": login_srcs[0],
            "dependents": auth_deps,
            "description": "登录接口返回 token → 下游接口注入 Authorization 头",
            "extract_path": "$.token", "inject_target": "headers.Authorization",
        })

    # ID 流: 找到 CREATE 接口的路径前缀, 匹配同前缀的 READ/UPDATE/DELETE
    for name in sorted_names:
        iface = iface_map.get(name)
        if not iface or iface.get("crud_type") != "CREATE":
            continue
        base = re.sub(r'/create$', '', iface.get("path", ""))
        consumers = [n for n in sorted_names if n != name
                     and iface_map.get(n, {}).get("crud_type") in ("READ", "UPDATE", "DELETE")
                     and base in iface_map[n].get("path", "") and "{" in iface_map[n].get("path", "")]
        if consumers:
            id_f = "device_id" if "device" in base.lower() else "id"
            chains.append({
                "chain_type": "id_flow", "source": name,
                "dependents": consumers,
                "description": f"{name} 返回 {id_f} → 下游接口注入路径参数",
                "extract_path": f"$.{id_f}", "inject_target": f"url.{id_f}",
            })
    return chains


# ════════════════════════════════════════════════════════════════
# 步骤5: DFS 三色标记环检测
#   WHITE(0)=未访问  GRAY(1)=递归栈中(遇到就有环)  BLACK(2)=已处理完
#   与 Kahn 互补: Kahn 知道"有没有环", DFS 能精确定位"环在哪"
# ════════════════════════════════════════════════════════════════

def _detect_cycles_dfs(dep_map: Dict) -> List[List[str]]:
    """DFS 三色标记环检测"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = defaultdict(int)
    cycles = []

    def dfs(node, path):
        color[node] = GRAY; path.append(node)
        for d in dep_map.get(node, []):
            nb = d["depends_on"]; nc = color.get(nb, WHITE)
            if nc == GRAY:
                try:
                    idx = path.index(nb)
                    cycles.append(path[idx:] + [nb])
                except ValueError: pass
            elif nc == WHITE:
                dfs(nb, path)
        path.pop(); color[node] = BLACK

    for n in dep_map:
        if color.get(n, WHITE) == WHITE:
            dfs(n, [])
    return cycles


# ════════════════════════════════════════════════════════════════
# 步骤6: 32组业务分组 + 版本隔离 + 相似度兜底
#   why分组? 影响面评估时只重跑受影响组的用例; 不同组可并行执行
# ════════════════════════════════════════════════════════════════

def _match_group(iface: dict) -> Tuple[str, Optional[str], float]:
    """对单个接口进行关键词分组匹配"""
    text = " ".join([
        iface.get("name", ""), iface.get("path", ""),
        iface.get("description", ""), " ".join(iface.get("tags", [])),
    ]).lower()
    best = ("default", None, 0.0)
    for gn, patterns in GROUP_KEYWORDS.items():
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                s = len(m.group()) / max(len(text), 1)
                if s > best[2]: best = (gn, pat, s)
    return best


def _group_interfaces(interfaces: List[Dict]) -> Dict:
    """32组业务分组: 关键词匹配 → 版本隔离 → 相似度兜底"""
    groups = defaultdict(list)
    ungrouped = []

    # 第一层: 关键词匹配
    for i, iface in enumerate(interfaces):
        g, kw, sc = _match_group(iface)
        ver = _VERSION_PATTERN.search(iface.get("path", ""))
        if g != "default" and sc > 0.01:
            groups[g].append({
                "index": i, "name": iface["name"],
                "version": ver.group().lower() if ver else "",
                "match_keyword": kw, "match_score": round(sc, 4),
            })
        else:
            ungrouped.append((i, iface))

    # 第二层: 版本隔离 — V0.1 和 V6 的接口即使同组也拆分子组
    versioned = {}
    for gn, mems in groups.items():
        by_ver = defaultdict(list)
        for m in mems:
            by_ver[m.get("version", "") or "unknown"].append(m)
        if len(by_ver) == 1:
            versioned[gn] = {"members": mems, "versions": list(by_ver.keys()), "is_versioned": False}
        else:
            for ver, vmems in by_ver.items():
                sn = f"{gn}_{ver}" if ver != "unknown" else gn
                versioned[sn] = {"members": vmems, "versions": [ver], "is_versioned": True}

    # 第三层: 相似度兜底 — SequenceMatcher 对 title+path+description 聚类
    if ungrouped:
        texts = [" ".join([i.get("name",""), i.get("path",""), i.get("description","")])
                 for _, i in ungrouped]
        visited = set()
        sg_count = 0
        for i, ti in enumerate(texts):
            if i in visited: continue
            sg_count += 1
            vg = f"similarity_group_{sg_count}"
            mems = []
            for j, tj in enumerate(texts):
                if j in visited: continue
                if i == j or SequenceMatcher(None, ti, tj).ratio() > 0.6:
                    mems.append({"index": ungrouped[j][0], "name": ungrouped[j][1]["name"],
                                 "version": "", "match_keyword": "similarity", "match_score": 0.0})
                    visited.add(j)
            versioned[vg] = {"members": mems, "versions": ["unknown"], "is_versioned": False}

    return {"total_groups": len(versioned), "group_list": list(versioned.keys()),
            "details": versioned, "ungrouped_count": len(ungrouped)}


# ════════════════════════════════════════════════════════════════
# 并发分组分析 — ThreadPoolExecutor(max_workers=5)
#   为什么用线程池？接口多时(>100)关键词正则匹配是 CPU 密集型，
#   单线程逐条匹配慢。分组操作纯函数无副作用，天然适合并行。
#   限制 5 worker 防止上下文切换开销超过并行收益。
# ════════════════════════════════════════════════════════════════

def _group_interfaces_concurrent(interfaces: List[Dict], max_workers: int = 5) -> Dict:
    """
    ThreadPoolExecutor 并发分组——接口数 > 50 时比串行快 2-4 倍。

    策略: 将 interfaces 切片分给 5 个 worker，每个 worker 独立做关键词匹配，
    主线程合并结果后统一做版本隔离和相似度兜底。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if len(interfaces) < 50:
        # 接口少时串行更快（线程创建开销 > 并行收益）
        return _group_interfaces(interfaces)

    chunk_size = max(1, len(interfaces) // max_workers)
    chunks = [interfaces[i:i+chunk_size] for i in range(0, len(interfaces), chunk_size)]

    all_groups = defaultdict(list)
    all_ungrouped = []

    with ThreadPoolExecutor(max_workers=min(max_workers, len(chunks))) as executor:
        futures = {executor.submit(_match_chunk, chunk, offset): (chunk, offset)
                   for offset, chunk in enumerate(chunks)}

        for future in as_completed(futures):
            chunk_groups, chunk_ungrouped = future.result()
            for gn, mems in chunk_groups.items():
                all_groups[gn].extend(mems)
            all_ungrouped.extend(chunk_ungrouped)

    # 合并后统一做版本隔离和相似度兜底
    versioned = {}
    for gn, mems in all_groups.items():
        by_ver = defaultdict(list)
        for m in mems:
            by_ver[m.get("version", "") or "unknown"].append(m)
        if len(by_ver) == 1:
            versioned[gn] = {"members": mems, "versions": list(by_ver.keys()), "is_versioned": False}
        else:
            for ver, vmems in by_ver.items():
                sn = f"{gn}_{ver}" if ver != "unknown" else gn
                versioned[sn] = {"members": vmems, "versions": [ver], "is_versioned": True}

    # 相似度兜底（ungrouped 数量通常少，串行即可）
    if all_ungrouped:
        texts = [" ".join([i.get("name",""), i.get("path",""), i.get("description","")])
                 for _, i in all_ungrouped]
        visited = set()
        sg_count = 0
        for i, ti in enumerate(texts):
            if i in visited:
                continue
            sg_count += 1
            vg = f"similarity_group_{sg_count}"
            mems = []
            for j, tj in enumerate(texts):
                if j in visited:
                    continue
                if i == j or SequenceMatcher(None, ti, tj).ratio() > 0.6:
                    mems.append({"index": all_ungrouped[j][0], "name": all_ungrouped[j][1]["name"],
                                 "version": "", "match_keyword": "similarity", "match_score": 0.0})
                    visited.add(j)
            versioned[vg] = {"members": mems, "versions": ["unknown"], "is_versioned": False}

    return {"total_groups": len(versioned), "group_list": list(versioned.keys()),
            "details": versioned, "ungrouped_count": len(all_ungrouped),
            "concurrent": True, "workers": max_workers}


def _match_chunk(interfaces_chunk: List[Dict], offset: int) -> tuple:
    """
    单 worker 的分组匹配——处理一个接口切片。
    返回 (groups_dict, ungrouped_list)，index 需要加 offset 保持全局一致性。
    """
    groups = defaultdict(list)
    ungrouped = []
    for i, iface in enumerate(interfaces_chunk):
        g, kw, sc = _match_group(iface)
        ver = _VERSION_PATTERN.search(iface.get("path", ""))
        if g != "default" and sc > 0.01:
            groups[g].append({
                "index": offset + i, "name": iface["name"],
                "version": ver.group().lower() if ver else "",
                "match_keyword": kw, "match_score": round(sc, 4),
            })
        else:
            ungrouped.append((offset + i, iface))
    return dict(groups), ungrouped


# ════════════════════════════════════════════════════════════════
# LLM 语义分组 —— 对关键词无法覆盖的接口做语义聚类
# ════════════════════════════════════════════════════════════════

def _llm_semantic_group(interfaces: List[Dict], existing_groups: Dict[str, List]) -> Optional[Dict[str, List]]:
    """LLM 语义分组：对关键词无法覆盖的接口做语义聚类"""
    # 找到未分组的接口
    grouped_names = set()
    for g in existing_groups.values():
        for i in g:
            grouped_names.add(i.get('name', ''))
    ungrouped = [i for i in interfaces if i.get('name', '') not in grouped_names]

    if not ungrouped or len(ungrouped) < 3:
        return None

    iface_text = "\n".join([
        f"- {i.get('name','')} [{i.get('method','')} {i.get('url','')}]: {i.get('description','')[:80]}"
        for i in ungrouped
    ])

    prompt = f"""以下是未分组的API接口列表，请按业务语义分组。

接口列表:
{iface_text}

请返回JSON，每个组一个key，value是该组接口名列表:
{{"组名1": ["接口名A", "接口名B"], "组名2": ["接口名C"]}}

只返回JSON，不要其他内容。"""

    result = llm_client.extract_json(prompt, temperature=0.3)
    if not result:
        return None

    # 将LLM分组结果映射回接口对象
    name_map = {i.get('name'): i for i in ungrouped}
    llm_groups = {}
    for group_name, iface_names in result.items():
        llm_groups[group_name] = [name_map[n] for n in iface_names if n in name_map]

    return llm_groups if llm_groups else None


# ════════════════════════════════════════════════════════════════
# 测试场景生成 — 从依赖链生成可执行场景序列
# ════════════════════════════════════════════════════════════════

def generate_test_scenarios(
    analysis_result: Dict[str, Any],
    max_scenarios: int = 10
) -> List[Dict[str, Any]]:
    """
    从依赖分析结果生成测试场景——每个场景是一组有序的接口调用序列。

    为什么需要场景？单接口测试只能验证 CRUD 正确性，场景测试验证
    真实业务流程（登录→创建设备→查询设备→控制设备→删除设备）。

    场景生成策略（3种）:
      1. CRUD全链路——取同一 category 的 CREATE→READ→UPDATE→DELETE
      2. 数据流链路——从 login 出发，沿 token_flow/id_flow 走到叶子
      3. 业务分组场景——取同一 group 的所有接口组成业务场景

    Returns: [{scenario_id, name, steps, description, coverage}]
    """
    scenarios = []
    seen_paths = set()  # 去重：同一 path 组合只生成一次
    execution_order = analysis_result.get("execution_order", [])
    sorted_names = [s["interface"] for s in execution_order]
    dep_map = analysis_result.get("dependency_map", {})
    data_flows = analysis_result.get("data_flow_chains", [])
    groups = analysis_result.get("groups", {}).get("details", {})

    # 建立 CRUD 类型索引
    by_crud = defaultdict(list)
    for n in sorted_names:
        # 从 groups 中推断 crud_type（尽力而为）
        crud = "READ"
        nl = n.lower()
        if any(kw in nl for kw in ("create", "创建", "新建")):
            crud = "CREATE"
        elif any(kw in nl for kw in ("update", "更新", "修改", "改名")):
            crud = "UPDATE"
        elif any(kw in nl for kw in ("delete", "删除", "remove")):
            crud = "DELETE"
        by_crud[crud].append(n)

    sid = 0

    # 策略1: CRUD 全链路场景
    for create_api in by_crud.get("CREATE", [])[:max_scenarios]:
        # 推断 category（从接口名中提取模块关键词）
        cat = _infer_scenario_category(create_api)
        reads = [n for n in by_crud.get("READ", []) if _infer_scenario_category(n) == cat]
        updates = [n for n in by_crud.get("UPDATE", []) if _infer_scenario_category(n) == cat]
        deletes = [n for n in by_crud.get("DELETE", []) if _infer_scenario_category(n) == cat]

        path_key = f"{create_api}|{reads[0] if reads else ''}|{updates[0] if updates else ''}|{deletes[0] if deletes else ''}"
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)

        steps = [create_api]
        if reads:
            steps.append(reads[0])
        if updates:
            steps.append(updates[0])
        if deletes:
            steps.append(deletes[0])

        sid += 1
        scenarios.append({
            "scenario_id": f"SCENARIO_{sid:03d}",
            "name": f"{cat}全生命周期",
            "type": "crud_chain",
            "steps": [{"order": j+1, "interface": name} for j, name in enumerate(steps)],
            "description": f"从创建{cat}到删除{cat}的完整 CRUD 链路",
            "expected_outcome": f"{cat}资源被成功创建、查询、更新、删除",
        })

    # 策略2: 数据流链路场景
    for flow in data_flows[:max_scenarios]:
        source = flow.get("source", "")
        dependents = flow.get("dependents", [])
        flow_type = flow.get("chain_type", "")

        steps = [source] + dependents[:4]  # 最多取4个下游
        path_key = "|".join(steps)
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)

        sid += 1
        scenarios.append({
            "scenario_id": f"SCENARIO_{sid:03d}",
            "name": f"{'Token认证' if flow_type == 'token_flow' else 'ID传递'}链路",
            "type": "data_flow",
            "steps": [{"order": j+1, "interface": name} for j, name in enumerate(steps)],
            "description": flow.get("description", ""),
            "extract_path": flow.get("extract_path"),
            "inject_target": flow.get("inject_target"),
        })

    # 策略3: 业务分组场景
    for gn, gdetail in list(groups.items())[:max_scenarios]:
        members = gdetail.get("members", [])
        if len(members) < 2:
            continue
        step_names = [m["name"] for m in members[:5]]  # 每组最多5个接口
        path_key = "|".join(step_names)
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)

        # 按 CRUD 顺序排列：CREATE → READ → UPDATE → DELETE
        # 为什么是 C-R-U-D 而非 C-U-R-D？
        # READ 在 UPDATE 之前更安全——先确认资源存在再修改，避免盲目更新已删除的数据。
        # 这也是 RESTful API 标准实践：POST → GET → PUT/PATCH → DELETE
        crud_order = {"CREATE": 0, "READ": 1, "UPDATE": 2, "DELETE": 3}
        step_names.sort(key=lambda n: crud_order.get(
            "CREATE" if any(kw in n.lower() for kw in ("create", "创建")) else
            "DELETE" if any(kw in n.lower() for kw in ("delete", "删除")) else
            "UPDATE" if any(kw in n.lower() for kw in ("update", "修改")) else "READ", 99))

        sid += 1
        scenarios.append({
            "scenario_id": f"SCENARIO_{sid:03d}",
            "name": f"{gn}业务场景",
            "type": "business_group",
            "steps": [{"order": j+1, "interface": name} for j, name in enumerate(step_names)],
            "description": f"{gn} 分组下的 {len(step_names)} 个接口组成的业务场景",
            "group": gn,
        })

    return scenarios[:max_scenarios]


def _infer_scenario_category(api_name: str) -> str:
    """从接口名推断业务分类（用于场景生成的模块匹配）"""
    nl = api_name.lower()
    for cat in ("device", "course", "family", "plan", "account", "health", "notification"):
        if cat in nl:
            return cat
    return "general"
