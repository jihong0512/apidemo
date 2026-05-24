"""
demo_05 智能数据工厂 — 核心逻辑 v2.0
对应课件: 第05讲 智能数据工厂
源码参考: smart_test_data_generator.py (625行) + advanced_data_generator.py (486行)

四层数据模型:
  L1 原子数据: Faker/builtin 生成的原始值 (phone=138xxxx, email=user@example.com)
  L2 接口数据: 按接口 schema 组装 params/headers/body
  L3 链数据:   DAG 执行上下文 (token 从 login 流向所有接口, device_id 从 create 流向 CRUD)
  L4 场景数据: 4类用例 (positive/boundary/negative/invalid)

设计原则: Faker 可用时优先使用，不可用时降级到内置生成器; 语义化字段推断;
          边界值按字段类型自动生成 (空串/零值/超长串/SQL注入字符)
"""
import random, time, uuid, json, re, sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import add_project_to_sys_path, Config
add_project_to_sys_path(__file__)

# ── Faker 尝试导入 ──────────────────────────────────────────────
try:
    from faker import Faker
    _faker = Faker("zh_CN")
    _faker_ok = True
except ImportError:
    _faker = None
    _faker_ok = False

# ── 内置 base64 编码 (生成 mock JWT，避免依赖 PyJWT) ──────────
def _b64enc(data: bytes) -> str:
    """简易 base64 编码 —— 仅用于 mock token，不依赖外部库"""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    res = []
    for i in range(0, len(data), 3):
        c = data[i:i+3]; n = (c[0]<<16)|(c[1]<<8 if len(c)>1 else 0)|(c[2] if len(c)>2 else 0)
        res.extend([chars[(n>>18)&63], chars[(n>>12)&63], chars[(n>>6)&63] if len(c)>1 else "=", chars[n&63] if len(c)>2 else "="])
    return "".join(res)

# ════════════════════════════════════════════════════════════════
# L1: Faker 字段模式映射 (from smart_test_data_generator.py L23-51)
#   根据字段名中的关键词推断语义类型，自动匹配合适的生成函数
#   为什么不用 LLM? 字段级生成用 Faker 更快、更确定、零 API 成本
# ════════════════════════════════════════════════════════════════
FIELD_PATTERNS: Dict[str, Callable[[], Any]] = {
    "phone": lambda: _faker.phone_number() if _faker_ok else f"138{random.randint(10000000,99999999)}",
    "mobile": lambda: _faker.phone_number() if _faker_ok else f"139{random.randint(10000000,99999999)}",
    "email": lambda: _faker.email() if _faker_ok else f"user{random.randint(1,9999)}@example.com",
    "username": lambda: _faker.user_name() if _faker_ok else f"user_{random.randint(100,9999)}",
    "password": lambda: f"Pwd@{random.randint(10000,99999)}",
    "name": lambda: _faker.name() if _faker_ok else random.choice(
        ["张伟","王芳","李娜","刘洋","陈静","杨帆","赵敏","黄磊","周杰","吴鑫"]),
    "address": lambda: _faker.address() if _faker_ok else f"北京市朝阳区某某路{random.randint(1,200)}号",
    "id_card": lambda: f"{random.randint(110101,659004)}{random.randint(1980,2005):04d}{random.randint(1,12):02d}{random.randint(1,28):02d}{random.randint(1000,9999)}",
    "sn": lambda: f"SN{datetime.now().year}{random.randint(1000,9999)}",
    "serial_no": lambda: f"SN{datetime.now().year}{random.randint(1000,9999)}",
    "device_id": lambda: f"DEV-{uuid.uuid4().hex[:12].upper()}",
    "mac": lambda: ":".join(f"{random.randint(0,255):02X}" for _ in range(6)),
    "token": lambda: f"eyJ.{_b64enc(bytes(random.randint(0,255) for _ in range(32)))}.{_b64enc(bytes(random.randint(0,255) for _ in range(16)))}",
    "api_key": lambda: f"ak-{uuid.uuid4().hex}",
    "timestamp": lambda: int(time.time() * 1000),
    "version": lambda: f"v{random.randint(1,5)}.{random.randint(0,9)}.{random.randint(0,9)}",
    "created_at": lambda: datetime.now().isoformat(),
    "code": lambda: str(random.randint(100000,999999)),
    "verify_code": lambda: str(random.randint(100000,999999)),
    "status": lambda: random.choice(["active","inactive","pending"]),
    "type": lambda: random.choice(["跑步机","走步机","划船机","智能哑铃"]),
    "description": lambda: "自动化测试生成的描述文本",
    "price": lambda: round(random.uniform(99,99999), 2),
    "page": lambda: random.randint(1,100),
    "page_size": lambda: random.choice([10,20,50,100]),
    "url": lambda: f"https://www.example{random.randint(1,99)}.com/api",
}

def _match_pattern(field_name: str) -> Optional[Callable[[], Any]]:
    """根据字段名语义匹配生成函数 (优先精确匹配→包含匹配→降级)"""
    fl = field_name.lower()
    for kw, gen in FIELD_PATTERNS.items():
        if fl == kw or kw in fl:
            return gen
    return None


# ════════════════════════════════════════════════════════════════
# 核心公开函数
# ════════════════════════════════════════════════════════════════

def generate_test_data(
    interfaces: List[Dict[str, Any]],
    deps_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    智能测试数据生成: L1原子值 → L2接口组装 → L3链上下文 → L4场景分类
    """
    print(f"\n  [INFO] Faker: {'已安装 (zh_CN)' if _faker_ok else '未安装，使用内置生成器'}")

    # L3: 构建 DAG 执行上下文 —— 记录每个上游接口产出的值 (token/device_id/user_id)
    flow_ctx = _build_flow_context(deps_data)

    # 获取执行顺序 (来自 step_04 的拓扑排序结果)
    exec_order = []
    if deps_data:
        exec_order = deps_data.get("execution_order", [])
    if not exec_order:
        exec_order = [{"step": idx+1, "interface": iface["name"]} for idx, iface in enumerate(interfaces)]

    iface_map = {i["name"]: i for i in interfaces}
    test_data = {}

    # L2+L4: 按执行顺序生成每个接口的4类测试数据
    for item in exec_order:
        name = item["interface"]
        iface = iface_map.get(name)
        if not iface: continue
        test_data[name] = _gen_interface_cases(iface, flow_ctx)

    return {
        "test_data": test_data,
        "total_interfaces": len(test_data),
        "total_cases": sum(len(c) for c in test_data.values()),
        "cases_per_interface": {n: len(c) for n, c in test_data.items()},
        "flow_context": flow_ctx,
        "_meta_faker": _faker_ok,
    }


# ════════════════════════════════════════════════════════════════
# L3: DAG 执行上下文构建
#   上下文记录: 哪个接口产出什么值 → 哪些下游接口需要它
#   数据流向: login → token → 所有需要 auth 的接口
#             create_device → device_id → read/update/delete
# ════════════════════════════════════════════════════════════════

def _build_flow_context(deps_data: Optional[Dict]) -> Dict:
    """构建执行上下文: token + device_id + 变量池 + 依赖映射"""
    ctx = {"token": None, "user_id": random.randint(1, 1000),
           "device_id": None, "variables": {}, "dependencies": defaultdict(list)}
    if not deps_data: return dict(ctx)

    for chain in deps_data.get("data_flow_chains", []):
        ct = chain.get("chain_type", "")
        src = chain.get("source", "")
        if ct == "token_flow":
            ctx["token"] = _mock_jwt()
            ctx["variables"]["token"] = ctx["token"]
            for dep in chain.get("dependents", []):
                ctx["dependencies"][dep].append({
                    "from": src, "field": "token",
                    "inject_to": "headers.Authorization", "format": "Bearer {{token}}",
                })
        elif ct == "id_flow":
            if "device" in src.lower():
                ctx["device_id"] = f"DEV-{uuid.uuid4().hex[:12].upper()}"
                ctx["variables"]["device_id"] = ctx["device_id"]
            ep = chain.get("extract_path", "$.id")
            for dep in chain.get("dependents", []):
                ctx["dependencies"][dep].append({
                    "from": src, "field": ep.replace("$.", ""),
                    "inject_to": chain.get("inject_target", ""),
                })
    return dict(ctx)


def _mock_jwt() -> str:
    """生成 mock JWT (header.payload.signature 三段式)"""
    h = _b64enc(json.dumps({"alg":"HS256","typ":"JWT"}).encode())
    p = _b64enc(json.dumps({"sub":str(random.randint(1,1000)),"iat":int(time.time()),
                             "exp":int(time.time())+86400}).encode())
    s = _b64enc(bytes(random.randint(0,255) for _ in range(32)))
    return f"{h}.{p}.{s}"


# ════════════════════════════════════════════════════════════════
# L2+L4: 接口数据生成 + 4类场景
#   positive=合法值→期望2xx  boundary=边界值→期望2xx/4xx
#   negative=缺必填+错误类型→期望4xx  invalid=null+SQL注入→期望4xx
# ════════════════════════════════════════════════════════════════

def _gen_interface_cases(iface: Dict, ctx: Dict) -> List[Dict]:
    """为单个接口生成 4 类测试用例"""
    m = iface.get("method", "GET").upper()
    cases = [
        {"case_type": "positive", "description": f"正向测试: {iface['name']}",
         "params": _gen_params(iface, "positive"), "headers": _gen_headers(iface, ctx, "positive"),
         "body": _gen_body(iface, "positive"), "expected_status": _expected_status(m)},
        {"case_type": "boundary", "description": f"边界值测试: {iface['name']}",
         "params": _gen_params(iface, "boundary"), "headers": _gen_headers(iface, ctx, "boundary"),
         "body": _gen_body(iface, "boundary"), "expected_status": 200 if m=="GET" else _expected_status(m)},
        {"case_type": "negative", "description": f"负向测试(缺必填): {iface['name']}",
         "params": _gen_params(iface, "negative"), "headers": _gen_headers(iface, ctx, "negative"),
         "body": _gen_body(iface, "negative"), "expected_status": 400},
    ]
    if m in ("POST", "PUT", "PATCH"):
        cases.append({
            "case_type": "invalid", "description": f"无效数据测试: {iface['name']}",
            "params": {}, "headers": _gen_headers(iface, ctx, "invalid"),
            "body": _gen_body(iface, "invalid"), "expected_status": 400,
        })
    return cases


def _gen_params(iface: Dict, ct: str) -> Dict:
    ps = iface.get("params", {})
    if not ps: return {}
    res = {}
    for k, s in ps.items():
        if isinstance(s, dict):
            res[k] = _boundary_value(k, s) if ct=="boundary" else _negative_value(k, s) if ct=="negative" else _smart_value(k, s)
        elif isinstance(s, (str,int,float,bool)): res[k] = s
        else: res[k] = _smart_value(k, {})
    return res


def _gen_headers(iface: Dict, ctx: Dict, ct: str) -> Dict:
    """生成请求头: positive/boundary 注入 token; negative 去掉认证头"""
    if ct == "negative": return {"Content-Type": "application/json"}
    h = {}
    for k, v in iface.get("headers", {}).items():
        if isinstance(v, str) and "{{" in v:
            rv = v
            for ck, cv in ctx.get("variables", {}).items():
                rv = rv.replace(f"{{{{{ck}}}}}", str(cv or ""))
            h[k] = rv
        else: h[k] = v
    return h


def _gen_body(iface: Dict, ct: str) -> Dict:
    """生成请求体: positive=合法值 boundary=边界值 negative=缺首必填 invalid=全null"""
    sc = iface.get("body", {}).get("schema", {})
    props = sc.get("properties", {})
    if not props: return {}
    req = sc.get("required", [])
    if ct == "positive": return {k: _smart_value(k, p) for k, p in props.items()}
    if ct == "boundary": return {k: _boundary_value(k, p) for k, p in props.items()}
    if ct == "negative":
        res = {}; skip = False
        for k, p in props.items():
            if not skip and k in req: skip = True; continue
            res[k] = _smart_value(k, p)
        return res
    if ct == "invalid": return {k: None for k in props}
    return {}


# ════════════════════════════════════════════════════════════════
# 值生成器: 正向 / 边界 / 负向 / 类型默认
# ════════════════════════════════════════════════════════════════

def _smart_value(field: str, schema: Dict) -> Any:
    """智能值: example > enum[0] > 字段名语义 > 类型默认"""
    if "example" in schema and schema["example"] is not None:
        return schema["example"]
    if "enum" in schema: return schema["enum"][0]
    gen = _match_pattern(field)
    if gen: return gen()
    return _type_default(schema.get("type", "string"))


def _boundary_value(field: str, schema: Dict) -> Any:
    """
    边界值 —— 大量 Bug 藏在边界条件中
    string: 空串 / 超长 / Unicode / SQL注入 / XSS
    integer: 0 / -1 / INT_MAX / INT_MIN
    """
    t = schema.get("type", "string")
    if t == "string":
        return random.choice(["", "A"*256, "测试Unicode🎯", "' OR '1'='1", "<script>alert(1)</script>"])
    if t == "integer": return random.choice([0, -1, 2147483647, -2147483648])
    if t == "number": return random.choice([0.0, -0.01, 999999999.99])
    if t == "boolean": return False
    return ""


def _negative_value(field: str, schema: Dict) -> Any:
    """负向值: 故意给错误类型 —— 测试框架的类型强制转换防御"""
    t = schema.get("type", "string")
    if t == "string": return 12345678
    if t == "integer": return "not_a_number"
    if t == "number": return "invalid_decimal"
    if t == "boolean": return "not_bool"
    return None


def _type_default(t: str) -> Any:
    return {"string": f"auto_{random.randint(1000,9999)}", "integer": random.randint(1,1000),
            "number": round(random.uniform(0.01,10000.0),2), "boolean": True,
            "array": [], "object": {}}.get(t, "test_value")


def _expected_status(method: str) -> int:
    """HTTP 方法 → 期望成功状态码 (与 backend/test_executor.py 一致)"""
    return {"POST": 201, "GET": 200, "PUT": 200, "PATCH": 200, "DELETE": 204}.get(method.upper(), 200)


# ════════════════════════════════════════════════════════════════
# AdvancedDataGenerator — L4 场景感知生成器
#   (backend/advanced_data_generator.py L1-486 同款)
#
#   与 generate_test_data() 的区别:
#     generate_test_data() 是"批量模式"——传入全部接口，一次生成完
#     AdvancedDataGenerator 是"场景模式"——感知执行上下文，
#     知道当前是 CREATE/READ/UPDATE/DELETE 哪一步，
#     CREATE 产出的 ID 会正确注入到 READ/UPDATE/DELETE 中。
#
#   为什么需要场景感知？批模式下 CREATE→device_id=DEV-AAA，
#   READ 接口可能拿到 DEV-BBB，数据不连贯。场景模式保证
#   同一场景内 CREATE 产出的 ID 就是 READ 要查的 ID。
# ════════════════════════════════════════════════════════════════

class AdvancedDataGenerator:
    """
    L4 场景感知数据生成器——维护一个场景变量池，保证场景内数据连贯。

    使用示例:
        gen = AdvancedDataGenerator(interfaces, deps_data)
        cases = gen.generate_for_scenario("设备全生命周期", [
            "createDevice", "getDeviceDetail", "updateDevice", "deleteDevice"
        ])
    """

    def __init__(self, interfaces: List[Dict], deps_data: Optional[Dict] = None):
        self.iface_map = {i["name"]: i for i in interfaces}
        self.deps_data = deps_data or {}
        # 场景变量池——CREATE 产出的值存入，READ/UPDATE/DELETE 从中取用
        self._scene_pool: Dict[str, Any] = {}
        # 执行上下文
        self._flow_ctx = _build_flow_context(deps_data)
        # 已生成计数
        self._gen_count = 0

    def generate_for_scenario(
        self, scenario_name: str, step_names: List[str],
        case_types: Optional[List[str]] = None
    ) -> List[Dict]:
        """
        按场景生成有序的测试数据。
        每个 step 产出的值自动注入到场景变量池，下游 step 可直接使用。

        Args:
            scenario_name: 场景名称
            step_names: 有序的接口名称列表 (如 ["login","createDevice","getDevice"])
            case_types: 要生成的用例类型，默认 ["positive","boundary","negative"]

        Returns: [{step, interface, case_type, params, headers, body, expected_status}]
        """
        if case_types is None:
            case_types = ["positive", "boundary", "negative"]

        self._scene_pool = {}  # 新场景重置变量池
        results = []

        for step_idx, name in enumerate(step_names):
            iface = self.iface_map.get(name)
            if not iface:
                continue

            for ct in case_types:
                case = self._generate_case(iface, ct, step_idx, scenario_name)
                results.append(case)

                # 正向用例产出值注入场景池（如 CREATE 返回 device_id）
                if ct == "positive":
                    self._extract_and_pool(iface, case)

        return results

    def _generate_case(self, iface: Dict, case_type: str, step_idx: int, scenario: str) -> Dict:
        """生成单个用例——注入场景变量池中的上游产出值"""
        ctx = dict(self._flow_ctx)
        # 合并场景变量池到上下文
        ctx["variables"].update(self._scene_pool)

        case = {
            "step": step_idx + 1,
            "scenario": scenario,
            "interface": iface["name"],
            "case_type": case_type,
            "description": f"[场景:{scenario}] {iface['name']} - {case_type}",
            "params": _gen_params_scene(iface, case_type, self._scene_pool),
            "headers": _gen_headers_scene(iface, ctx, case_type, self._scene_pool),
            "body": _gen_body_scene(iface, case_type, self._scene_pool),
            "expected_status": _expected_status(iface.get("method", "GET")),
        }

        # negative/invalid 覆盖期望状态码
        if case_type == "negative":
            case["expected_status"] = 400
        elif case_type == "invalid":
            case["expected_status"] = 400

        self._gen_count += 1
        return case

    def _extract_and_pool(self, iface: Dict, case: Dict):
        """从正向用例的生成数据中提取关键 ID，注入场景池"""
        m = iface.get("method", "GET").upper()
        name = iface["name"].lower()

        # CREATE 接口产出 ID
        if m == "POST" and any(kw in name for kw in ("create", "注册", "新建", "添加")):
            body = case.get("body", {})
            for id_field in ("id", "device_id", "user_id", "plan_id", "course_id", "family_id"):
                val = body.get(id_field) or case.get("params", {}).get(id_field)
                if val:
                    self._scene_pool[id_field] = val
                    # 同时存一个通用 "id"
                    if id_field != "id":
                        self._scene_pool.setdefault("id", val)
                    break

        # LOGIN 接口产出 token
        if "login" in name or "登录" in name or "auth" in name:
            self._scene_pool["token"] = _mock_jwt()
            self._scene_pool["user_id"] = random.randint(1, 1000)

    def generate_for_api(
        self, api_info: Dict, case_types: Optional[List[str]] = None
    ) -> Dict:
        """
        7 步流水线——为单个接口生成完整测试数据 (generate_test_data_for_api 的类方法版本)

        步骤: Schema解析 → 字段匹配 → L1原子值 → L2接口组装 → L3上下文注入 → L4场景分类 → 组装输出
        """
        return generate_test_data_for_api(api_info, case_types, self._scene_pool)

    @property
    def stats(self) -> Dict:
        return {"generated_cases": self._gen_count, "pool_variables": list(self._scene_pool.keys())}


# ════════════════════════════════════════════════════════════════
# 场景感知的值生成器 (AdvancedDataGenerator 专用)
#   与 _gen_params/_gen_headers/_gen_body 的区别:
#   场景版本会从 scene_pool 中读取上游接口产出的真实 ID，
#   而非每次都随机生成新值。
# ════════════════════════════════════════════════════════════════

def _gen_params_scene(iface: Dict, case_type: str, scene_pool: Dict) -> Dict:
    """场景感知的参数生成——优先使用 scene_pool 中的 ID"""
    ps = iface.get("params", {})
    if not ps:
        return {}

    res = {}
    for k, s in ps.items():
        if isinstance(s, dict):
            # 先尝试从场景池取（如 device_id=DEV-AAA）
            if k in scene_pool and case_type == "positive":
                res[k] = scene_pool[k]
            elif case_type == "boundary":
                res[k] = _boundary_value(k, s)
            elif case_type == "negative":
                res[k] = _negative_value(k, s)
            else:
                res[k] = _smart_value(k, s)
        elif isinstance(s, (str, int, float, bool)):
            res[k] = scene_pool.get(k, s)
        else:
            res[k] = _smart_value(k, {})
    return res


def _gen_headers_scene(iface: Dict, ctx: Dict, case_type: str, scene_pool: Dict) -> Dict:
    """场景感知的 header 生成——变量替换使用场景池优先"""
    if case_type == "negative":
        return {"Content-Type": "application/json"}

    h = {}
    all_vars = {**ctx.get("variables", {}), **scene_pool}
    for k, v in iface.get("headers", {}).items():
        if isinstance(v, str) and "{{" in v:
            rv = v
            for ck, cv in all_vars.items():
                rv = rv.replace(f"{{{{{ck}}}}}", str(cv or ""))
            h[k] = rv
        else:
            h[k] = v
    return h


def _gen_body_scene(iface: Dict, case_type: str, scene_pool: Dict) -> Dict:
    """场景感知的 body 生成——优先使用场景池 ID"""
    sc = iface.get("body", {}).get("schema", {})
    props = sc.get("properties", {})
    if not props:
        return {}

    req = sc.get("required", [])

    if case_type == "positive":
        res = {}
        for k, p in props.items():
            if k in scene_pool:
                res[k] = scene_pool[k]  # 使用场景池中的上游 ID
            else:
                res[k] = _smart_value(k, p)
        return res

    if case_type == "boundary":
        return {k: _boundary_value(k, p) for k, p in props.items()}

    if case_type == "negative":
        res = {}
        skip = False
        for k, p in props.items():
            if not skip and k in req:
                skip = True
                continue
            res[k] = _smart_value(k, p)
        return res

    if case_type == "invalid":
        return {k: None for k in props}

    return {}


# ════════════════════════════════════════════════════════════════
# 7 步流水线: generate_test_data_for_api() (单接口版本)
#   步骤1-7 对应四层数据模型的完整生成流程
# ════════════════════════════════════════════════════════════════

def generate_test_data_for_api(
    api_info: Dict,
    case_types: Optional[List[str]] = None,
    scene_pool: Optional[Dict] = None
) -> Dict:
    """
    7 步流水线——为单个接口生成完整测试数据。

    步骤:
      1. Schema提取——从 api_info 提取 params/headers/body schema
      2. 字段模式匹配——FIELD_PATTERNS 关键词匹配 + 类型推断
      3. L1原子值生成——每个字段生成原始值
      4. L2接口组装——按 method 组装 params+headers+body
      5. L3上下文注入——变量替换 {{token}} {{device_id}}
      6. L4场景分类——positive/boundary/negative/invalid
      7. 组装输出——统一格式返回

    Args:
        api_info: 单个接口的标准化字典
        case_types: 要生成的用例类型列表，默认全部4种
        scene_pool: 场景变量池 (上游接口产出的值)

    Returns: {api_name, cases, field_mapping, pipeline_log}
    """
    if case_types is None:
        case_types = ["positive", "boundary", "negative", "invalid"]

    pool = scene_pool or {}
    pipeline_log = []
    method = api_info.get("method", "GET").upper()

    # 步骤1: Schema提取
    params_schema = api_info.get("params", {})
    headers_schema = api_info.get("headers", {})
    body_schema = api_info.get("body", {}).get("schema", {})
    body_props = body_schema.get("properties", {})
    body_required = body_schema.get("required", [])
    pipeline_log.append({"step": 1, "name": "Schema提取",
                         "params_count": len(params_schema),
                         "body_fields": list(body_props.keys())})

    # 步骤2: 字段模式匹配
    field_mapping = {}
    all_fields = list(params_schema.keys()) + list(body_props.keys())
    for field in all_fields:
        gen = _match_pattern(field)
        if gen:
            field_mapping[field] = {
                "matched": True,
                "generator": gen.__name__ if hasattr(gen, '__name__') else str(gen),
            }
        else:
            field_type = body_props.get(field, {}).get("type", "string")
            field_mapping[field] = {"matched": False, "fallback_type": field_type}
    pipeline_log.append({"step": 2, "name": "字段模式匹配",
                         "total_fields": len(all_fields),
                         "matched": sum(1 for v in field_mapping.values() if v["matched"]),
                         "unmatched": sum(1 for v in field_mapping.values() if not v["matched"])})

    # 步骤3: L1原子值生成
    l1_values = {}
    for field in all_fields:
        gen = _match_pattern(field)
        if gen:
            l1_values[field] = gen()
        else:
            schema_info = body_props.get(field, params_schema.get(field, {}))
            if isinstance(schema_info, dict):
                l1_values[field] = _type_default(schema_info.get("type", "string"))
            else:
                l1_values[field] = schema_info if schema_info else f"auto_{random.randint(1000,9999)}"
    pipeline_log.append({"step": 3, "name": "L1原子值生成",
                         "values": {k: str(v)[:30] for k, v in l1_values.items()}})

    # 步骤4: L2接口组装 (正向基准)
    base_params = {k: pool.get(k, l1_values.get(k, v if isinstance(v, (str, int, float, bool)) else _smart_value(k, {} if not isinstance(v, dict) else v)))
                   for k, v in params_schema.items()} if params_schema else {}
    base_headers = dict(headers_schema) if headers_schema else {"Content-Type": "application/json"}
    base_body = {k: pool.get(k, l1_values.get(k, _smart_value(k, p)))
                 for k, p in body_props.items()} if body_props else {}
    pipeline_log.append({"step": 4, "name": "L2接口组装",
                         "params": base_params, "body_keys": list(base_body.keys())})

    # 步骤5: L3上下文注入——变量替换
    all_vars = {**pool}
    for k, v in base_headers.items():
        if isinstance(v, str) and "{{" in v:
            for ck, cv in all_vars.items():
                v = v.replace(f"{{{{{ck}}}}}", str(cv or ""))
            base_headers[k] = v
    pipeline_log.append({"step": 5, "name": "L3上下文注入",
                         "variables_available": list(all_vars.keys())})

    # 步骤6+7: L4场景分类 + 组装输出
    cases = []
    for ct in case_types:
        case = {
            "case_type": ct,
            "description": f"{api_info['name']} - {ct}",
            "params": _gen_params_scene(api_info, ct, pool) if ct != "positive" else base_params,
            "headers": _gen_headers_scene(api_info, {"variables": all_vars}, ct, pool) if ct != "positive" else base_headers,
            "body": _gen_body_scene(api_info, ct, pool) if ct != "positive" else base_body,
            "expected_status": _expected_status(method) if ct == "positive" else 400,
        }
        # positive 保持原期望状态码
        if ct == "positive":
            case["expected_status"] = _expected_status(method)
        elif ct == "boundary" and method == "GET":
            case["expected_status"] = 200
        cases.append(case)

    pipeline_log.append({"step": 6, "name": "L4场景分类",
                         "case_types": case_types})
    pipeline_log.append({"step": 7, "name": "组装输出",
                         "total_cases": len(cases)})

    return {
        "api_name": api_info.get("name", "unknown"),
        "method": method,
        "path": api_info.get("path", ""),
        "cases": cases,
        "field_mapping": field_mapping,
        "pipeline_log": pipeline_log,
    }


# ════════════════════════════════════════════════════════════════
# Pytest 参数化代码生成
#   把 L4 场景数据转换为 @pytest.mark.parametrize 装饰器代码，
#   可直接粘贴到 pytest 测试文件中运行。
# ════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
# 正交试验 — 用正交表减少多参数组合用例数
# ══════════════════════════════════════════════════════════════

def generate_orthogonal_cases(
    interface_info: Dict[str, Any],
    factors: Dict[str, List[Any]],
    strength: int = 2
) -> List[Dict[str, Any]]:
    """
    正交试验生成器。

    当接口有 3 个以上参数、每个参数有多种取值时，
    全组合（笛卡尔积）会导致用例数爆炸。
    正交表选取有代表性的组合，大幅减少用例数同时保持覆盖率。

    Args:
        interface_info: 接口信息字典
        factors: 参数名→取值列表，如 {"page": [1,10,100], "size": [5,20,50], "sort": ["asc","desc"]}
        strength: 正交强度 (默认2=两两组合覆盖)
    Returns:
        正交用例列表
    """
    if not factors or len(factors) < 2:
        return []

    # 简单实现：取每个因素的前 N 个值做轮转组合
    # 生产环境可用 allpairspy 库做真正的全对偶覆盖
    factor_names = list(factors.keys())
    factor_values = [factors[f] for f in factor_names]

    cases = []
    # 用轮转法生成近似正交组合：每行是各因素轮转取值
    max_len = max(len(v) for v in factor_values)
    for i in range(min(max_len, 10)):  # 最多10个正交用例
        case_params = {}
        for j, name in enumerate(factor_names):
            vals = factor_values[j]
            case_params[name] = vals[i % len(vals)]

        cases.append({
            "case_type": "orthogonal",
            "description": f"正交试验用例 {i+1}: {case_params}",
            "params": {},
            "headers": {"Content-Type": "application/json"},
            "body": case_params,
            "expected_status": 200,
        })

    return cases


def generate_pytest_parametrize_code(
    test_data: Dict,
    api_name: Optional[str] = None,
    include_description: bool = True
) -> str:
    """
    将测试数据字典生成 @pytest.mark.parametrize 代码。

    生成的代码可直接粘贴到 pytest 测试文件中，配合 requests 库发送请求。

    Args:
        test_data: generate_test_data() 或 generate_test_data_for_api() 的输出
        api_name: 指定接口名（None=全部接口）
        include_description: 是否在 ID 中包含描述

    Returns: 可直接执行的 pytest 测试代码字符串
    """
    lines = []
    lines.append("import pytest")
    lines.append("import requests")
    lines.append("")
    lines.append("# 自动生成的参数化测试用例 —— 由 demo_05 数据工厂生成")
    lines.append(f"# 生成时间: {datetime.now().isoformat()}")
    lines.append("")

    td = test_data.get("test_data", test_data.get("cases", {}))

    # 单接口模式
    if api_name or (isinstance(test_data, dict) and "cases" in test_data and "api_name" in test_data):
        if api_name:
            cases = td.get(api_name, [])
            if not cases:
                return f"# 未找到接口: {api_name}"
        else:
            cases = test_data.get("cases", [])
            api_name = test_data.get("api_name", "unknown")

        method = test_data.get("method", "GET")
        path = test_data.get("path", "/")

        # 构建 parametrize 参数
        ids = []
        params_list = []
        headers_list = []
        bodies_list = []
        expected_list = []

        for case in cases:
            case_type = case.get("case_type", "unknown")
            desc = case.get("description", case_type)
            ids.append(desc if include_description else case_type)
            params_list.append(case.get("params", {}))
            headers_list.append(case.get("headers", {}))
            bodies_list.append(case.get("body", {}))
            expected_list.append(case.get("expected_status", 200))

        lines.append(f'@pytest.mark.parametrize(')
        lines.append(f'    "params,headers,body,expected_status",')
        lines.append(f'    [')
        for p, h, b, e in zip(params_list, headers_list, bodies_list, expected_list):
            lines.append(f'        ({json.dumps(p, ensure_ascii=False)}, {json.dumps(h, ensure_ascii=False)}, {json.dumps(b, ensure_ascii=False)}, {e}),')
        lines.append(f'    ],')
        lines.append(f'    ids={json.dumps(ids, ensure_ascii=False)}')
        lines.append(f')')
        lines.append(f'def test_{api_name.replace(" ", "_").replace("-", "_").lower()}(params, headers, body, expected_status):')
        lines.append(f'    """测试接口: {api_name}"""')
        lines.append(f'    url = "{path}"')
        lines.append(f'    response = requests.{method.lower()}(')
        lines.append(f'        url, params=params, headers=headers, json=body')
        lines.append(f'    )')
        lines.append(f'    assert response.status_code == expected_status, f"期望{{expected_status}}，实际{{response.status_code}}"')
        lines.append(f'    if expected_status < 400:')
        lines.append(f'        assert response.json() is not None')

        return '\n'.join(lines)

    # 多接口模式
    for name, cases in td.items():
        if not isinstance(cases, list):
            continue
        lines.append(f"# ── {name} ──")
        lines.append("")
        # 递归调用单接口模式
        single_data = {
            "api_name": name,
            "cases": cases,
            "method": "GET",  # 尽力而为
            "path": "/",
        }
        lines.append(generate_pytest_parametrize_code(single_data, include_description=include_description))
        lines.append("")
        lines.append("")

    return '\n'.join(lines)
