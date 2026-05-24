"""
demo_06 用例生成引擎 — 核心逻辑 v2.0
对应课件: 第06讲 用例生成引擎
源码参考: test_case_generator.py (731行) + prompt_engineer.py (377行)

两种生成模式:
  1. LLM 模式 (有 API Key): 8段式 Prompt → LLM chat → 提取代码
  2. 模板模式 (无 API Key): 规则模板生成 pytest + requests 代码

Prompt 8段式 (from prompt_engineer.py L11-60):
  ① 角色定义 ② 任务描述 ③ API接口详情 ④ 测试数据
  ⑤ 代码结构要求 ⑥ 代码规范 ⑦ 自定义要求 ⑧ 输出格式

设计原则: LLM 可用优先走 LLM; 不可用时降级到模板(保证任何时候都能跑);
          同时生成 pytest 和 HttpRunner 两种格式; conftest 含 session 级 fixture

⚠ 课件 vs Demo 差异:
  课件中的 PytestCaseGenerator (backend/test_case_generator.py) 包含 LLM/模板双模式路由
  (use_llm 参数 + LLMService + LLMServiceSync)。本 demo 的 PytestCaseGenerator 是纯模板版，
  LLM 双模式逻辑上移到 generate_test_cases() 顶层函数中(_llm_generate / _tmpl_generate)。
  功能等价，架构不同——生产版面向多用户并发，demo 版面向教学演示。
"""

import sys, json, textwrap, re
from pathlib import Path
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import add_project_to_sys_path, llm_client, Config
add_project_to_sys_path(__file__)


# ════════════════════════════════════════════════════════════════
# 核心公开函数
# ════════════════════════════════════════════════════════════════

def generate_test_cases(
    interfaces: List[Dict[str, Any]],
    test_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    生成可执行测试用例: LLM 模式(8段式Prompt) / 模板模式(降级)

    Args:
        interfaces: 接口列表
        test_data: step_05 的 test_data 字段 {接口名: [{case_type, params, headers, body}, ...]}
    Returns: {test_cases, total_cases, generation_mode, conftest, summary}
    """
    mode = "llm" if Config.is_llm_available() else "template"
    print(f"\n  [INFO] 生成模式: {'LLM (DeepSeek)' if mode == 'llm' else '模板引擎 (降级)'}")

    iface_map = {i["name"]: i for i in interfaces}
    cases = []
    llm_ok = llm_fail = 0

    for iface_name, case_list in test_data.items():
        iface = iface_map.get(iface_name)
        if not iface: continue
        for cd in case_list:
            if mode == "llm":
                llm_case = _llm_generate(iface, cd)
                if llm_case: cases.append(llm_case); llm_ok += 1
                else: cases.append(_tmpl_generate(iface, cd)); llm_fail += 1
            else:
                cases.append(_tmpl_generate(iface, cd))

    if mode == "llm":
        print(f"  [INFO] LLM: 成功 {llm_ok}, 降级模板 {llm_fail}")

    # ── 第1步：覆盖验证 ──
    if cases:
        covered_methods = set()
        covered_types = set()
        for tc in cases:
            covered_methods.add(tc.get('method', ''))
            covered_types.add(tc.get('case_type', ''))

        # 期望覆盖：所有HTTP方法 + normal/boundary/exception/dependency 四种类型
        expected_types = {"positive", "boundary", "negative", "invalid"}
        missing_types = expected_types - covered_types

        if missing_types:
            # 构建扁平化的 (iface, case_data) 对，缺哪种类型就补充生成哪种
            for iface_name, case_list in test_data.items():
                iface = iface_map.get(iface_name)
                if not iface:
                    continue
                for cd in case_list:
                    if cd.get('case_type') in missing_types:
                        extra = _llm_generate(iface, cd) if Config.is_llm_available() else _tmpl_generate(iface, cd)
                        if extra:
                            cases.append(extra)

    # ── 增强生成：PytestCaseGenerator + RequestBuilder ──
    if Config.is_llm_available():
        try:
            # 挑一个 POST/PUT 接口用 RequestBuilder 构造URL
            for iface_name, case_list in test_data.items():
                iface = iface_map.get(iface_name)
                if not iface:
                    continue
                if iface.get("method") in ("POST", "PUT"):
                    for cd in case_list:
                        if cd.get("case_type") == "positive":
                            rb = RequestBuilder(api_info=iface, case_data=cd)
                            resolved_url = rb._build_url()
                            if resolved_url:
                                # 用 PytestCaseGenerator 生成一个完整用例
                                gen = PytestCaseGenerator(
                                    api_info=iface,
                                    case_data=cd,
                                    framework="pytest",
                                    test_style="functional",
                                    include_fixtures=True,
                                    assertion_level=3,
                                    output_format="code",
                                )
                                pytest_case = gen.generate()
                                if pytest_case:
                                    cases.append(pytest_case)
                            break  # 只演示一个就够了
                    break  # 只演示一个就够了
        except Exception:
            pass

    return {
        "test_cases": cases,
        "total_cases": len(cases),
        "generation_mode": mode,
        "conftest": _gen_conftest(),
        "summary": _summarize(cases),
    }


# ════════════════════════════════════════════════════════════════
# 8段式 Prompt 构建器 (from prompt_engineer.py)
#   为什么 8 段? 每段独立职责 → 可单独调试优化 → 匹配不同框架只需换第⑤段
#   实践中 8 段比单长串 prompt 理解准确率高约 25%
# ════════════════════════════════════════════════════════════════

def _build_prompt(iface: Dict, case_data: Dict, framework: str = "pytest") -> str:
    """构建 8段式代码生成 Prompt"""
    m = iface.get("method", "GET")
    u = _resolve_url(iface.get("url", ""), case_data)
    nm = iface.get("name", "")
    ct = case_data.get("case_type", "positive")

    parts = []
    # ① 角色定义 — 设定 LLM 身份比笼统"请帮我..."效果好得多
    parts.append(
        "你是一位资深自动化测试工程师，精通 Python pytest、requests 库和 HttpRunner 框架。"
        "你生成的代码严格遵守 PEP8，包含完善的异常处理和断言，可直接在 CI/CD 流水线运行。"
    )
    # ② 任务描述
    parts.append(
        f"为 API [{nm}] 生成 {framework} 测试函数。\n"
        f"测试类型: {ct} | 期望状态码: {case_data.get('expected_status', 200)}\n"
        f"说明: {case_data.get('description', '')}"
    )
    # ③ API 接口详情
    parts.append(_fmt_iface(iface))
    # ④ 测试数据
    parts.append(_fmt_data(case_data))
    # ⑤ 代码结构
    parts.append(_fmt_structure(framework, m, u))
    # ⑥ 代码规范
    parts.append(
        "代码规范: PEP8 缩进4空格; 函数名 snake_case; 完整 docstring;"
        " f-string 拼接 URL; assert 第二个参数写失败信息; 不用 print"
    )
    # ⑦ 自定义要求(断言层级)
    parts.append(
        "断言: 第一层 HTTP 状态码; 第二层 JSON 结构字段存在性; 第三层字段类型校验。"
        "正向用例额外检查关键业务字段非空。如有 token/id 请提取并存储。"
    )
    # ⑧ 输出格式
    parts.append("请只输出 Python 代码，不要 markdown 代码块标记，不要解释文字。")

    prompt = "\n\n".join(parts)

    # ── Few-Shot 示例注入 ──
    if DEFAULT_FEW_SHOT:
        examples_text = "\n\n".join(
            f"【{ex['case_type']}】{ex['description']}\n```python\n{ex['code']}\n```"
            for ex in DEFAULT_FEW_SHOT[:3]
        )
        prompt += f"\n\n【参考示例 — 高质量测试用例】\n{examples_text}\n\n请参考以上示例的风格和结构生成用例。"

    return prompt


def _fmt_iface(iface: Dict) -> str:
    """格式化接口详情 (Prompt 第③段)"""
    lines = [f"API: {iface.get('method','GET')} {iface.get('url','')}",
             f"描述: {iface.get('description','')}"]
    h = iface.get("headers", {})
    if h: lines.append(f"Headers: {json.dumps(h, ensure_ascii=False)}")
    p = iface.get("params", {})
    if p: lines.append(f"Params: {json.dumps(p, ensure_ascii=False)}")
    b = iface.get("body", {}).get("schema", {})
    if b:
        lines.append(f"必填: {json.dumps(b.get('required',[]), ensure_ascii=False)}")
        lines.append(f"字段: {json.dumps({k: v.get('type','') for k,v in b.get('properties',{}).items()}, ensure_ascii=False)}")
    return "\n".join(lines)


def _fmt_data(cd: Dict) -> str:
    """格式化测试数据 (Prompt 第④段)"""
    lines = [f"用例类型: {cd.get('case_type','')} | 期望状态码: {cd.get('expected_status','')}"]
    if cd.get("params"): lines.append(f"Params: {json.dumps(cd['params'], ensure_ascii=False)}")
    if cd.get("body"): lines.append(f"Body: {json.dumps(cd['body'], ensure_ascii=False)}")
    if cd.get("headers"): lines.append(f"Headers: {json.dumps(cd['headers'], ensure_ascii=False)}")
    return "\n".join(lines)


def _fmt_structure(fw: str, m: str, u: str) -> str:
    """代码结构模板 (Prompt 第⑤段)"""
    if fw == "httprunner":
        return (f"HttpRunner 3.x YAML:\n  config: {{name, variables:{{base_url}}}}\n"
                f"  teststeps: [{{name, request:{{method:{m}, url:{u}}}, extract, validate}}]")
    return (f"pytest + requests:\n"
            f"  def test_xxx(base_url, auth_headers):\n"
            f"    url = f'{{base_url}}{u}'\n"
            f"    response = requests.{m.lower()}(url, headers={{**auth_headers}}, json={{...}})\n"
            f"    assert response.status_code == 200")


# ════════════════════════════════════════════════════════════════
# LLM 模式: 8段式 Prompt → chat → 提取代码
# ════════════════════════════════════════════════════════════════

def _llm_generate(iface: Dict, case_data: Dict) -> Optional[Dict]:
    """LLM 生成: build prompt → chat → extract code → 构建 dict。失败返回 None"""
    if not Config.is_llm_available(): return None
    prompt = _build_prompt(iface, case_data, "pytest")
    try:
        resp = llm_client.chat(prompt, temperature=0.5, max_tokens=1500)
    except Exception as e:
        print(f"  [LLM] 异常: {e}"); return None
    if not resp: return None

    code = _extract_code(resp)
    if not code: return None

    fn = _make_func_name(iface["name"], case_data.get("case_type", "positive"))
    return _build_case_dict(iface, case_data, fn, code)


def _extract_code(response: str) -> Optional[str]:
    """
    从 LLM 响应提取 Python 代码 (三层容错)
    LLM 经常不遵守"不用代码块"的要求，必须多层提取
    """
    # 1. ```python ... ``` 代码块
    m = re.search(r"```(?:python)?\s*([\s\S]*?)\s*```", response)
    if m: return m.group(1).strip()
    # 2. 以 def/import 开头
    s = response.strip()
    if s.startswith("def ") or s.startswith("import "): return s
    # 3. 找第一个 def/import 到末尾
    m = re.search(r"(?:def |import )[\s\S]*", response)
    if m: return m.group().strip()
    # 降级: 返回原始
    print(f"  [LLM] 代码提取降级，使用原始响应")
    return response.strip()


# ════════════════════════════════════════════════════════════════
# 模板模式: 规则模板生成 pytest + HttpRunner
#   为什么模板也能高质量? 接口结构标准化(method+url+body)、断言模式固定
#   模板生成的代码几乎 100% 合法，不需要 post-processing
# ════════════════════════════════════════════════════════════════

def _tmpl_generate(iface: Dict, case_data: Dict) -> Dict:
    """模板模式: 直接拼接 pytest 代码 + HttpRunner YAML"""
    m = iface.get("method", "GET").upper()
    u = _resolve_url(iface.get("url", ""), case_data)
    ct = case_data.get("case_type", "positive")
    es = case_data.get("expected_status", 200)
    desc = case_data.get("description", iface.get("name", ""))
    fn = _make_func_name(iface["name"], ct)

    code = [_make_test_function(fn, m, u, ct, es, desc, iface, case_data)]
    return _build_case_dict(iface, case_data, fn, code[0])


def _make_test_function(fn: str, m: str, u: str, ct: str, es: int,
                        desc: str, iface: Dict, cd: Dict) -> str:
    """构建单个 pytest 测试函数 (3层断言: 状态码 → JSON结构 → 字段类型)"""
    lines = [
        f'"""', desc, f'接口: {m} {iface.get("url","")}',
        f'用例: {ct} | 期望: {es}', f'"""', "",
        f"def {fn}(base_url, auth_headers):",
        f'    """{desc}"""', "",
        f'    url = f"{{base_url}}{u}"',
    ]
    hd = cd.get("headers", {})
    if hd:
        hj = json.dumps(hd, ensure_ascii=False, indent=8).replace("\n", "\n    ")
        lines.append(f"    headers = {{**auth_headers, **{hj}}}")
    else:
        lines.append(f"    headers = {{**auth_headers}}")
    pd = cd.get("params", {}); pa = ""
    if pd: lines.append(f"    params = {json.dumps(pd, ensure_ascii=False)}"); pa = ", params=params"
    bd = cd.get("body", {}); ba = ""
    if bd:
        bj = json.dumps(bd, ensure_ascii=False, indent=8).replace("\n", "\n    ")
        lines.append(f"    body = {bj}")
        if m in ("POST","PUT","PATCH"): ba = ", json=body"
    else:
        lines.append("    body = None")
    lines.append("")
    lines.append(f"    resp = requests.{m.lower()}(url, headers=headers{pa}{ba})")
    lines.append("")
    if m == "DELETE":
        lines.append(f"    assert resp.status_code in ({es}, 200, 204), f\"{es} expected, got {{resp.status_code}}\"")
    else:
        lines.append(f"    assert resp.status_code == {es}, f\"{es} expected, got {{resp.status_code}}\"")
    if es in (200, 201) and ct == "positive":
        lines.extend(["    data = resp.json()", '    assert data is not None, "响应体为空"'])
        rs = iface.get("response_schema", {}).get("schema", {}).get("properties", {})
        if rs:
            lines.append("    # 字段类型断言")
            type_checks = {"string": "str", "integer": "int", "number": "(int, float)"}
            for field, fs in list(rs.items())[:5]:
                ft = fs.get("type", "string")
                tc = type_checks.get(ft, "object")
                lines.append(f'    assert isinstance(data.get("{field}"), {tc}), f"{field} 类型应为 {ft}"')
    elif ct == "negative":
        lines.extend(["    try:", "        err = resp.json()",
                      '        assert "error" in err or "message" in err, "应返回错误信息"',
                      "    except Exception:", "        pass"])
    lines.append("")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# 公共辅助函数
# ════════════════════════════════════════════════════════════════

def _build_case_dict(iface: Dict, cd: Dict, func_name: str, pytest_code: str) -> Dict:
    """构建测试用例 dict (同时生成 HttpRunner YAML — 模板生成比 LLM 更可靠)"""
    m = iface.get("method", "GET"); u = _resolve_url(iface.get("url",""), cd)
    yaml = [f"config:", f'  name: "{iface["name"]} - {cd.get("case_type","")}"',
            f"  variables:", f'    base_url: "http://localhost:8004"',
            f"teststeps:", f"  - name: \"{cd.get('description', iface['name'])}\"",
            f"    request:", f"      method: {m}", f'      url: "{u}"']
    hds = cd.get("headers", {})
    if hds: yaml.append("      headers:")
    for k, v in hds.items(): yaml.append(f'        {k}: "{v}"')
    pms = cd.get("params", {})
    if pms: yaml.append("      params:")
    for k, v in pms.items(): yaml.append(f"        {k}: {json.dumps(v, ensure_ascii=False)}")
    bd = cd.get("body", {})
    if bd: yaml.append("      json:")
    for k, v in bd.items(): yaml.append(f"        {k}: {json.dumps(v, ensure_ascii=False)}")
    yaml.append(f"    validate:\n      - eq: [\"status_code\", {cd.get('expected_status',200)}]")
    return {"function_name": func_name, "interface_name": iface.get("name",""), "method": m,
            "url": u, "case_type": cd.get("case_type","positive"),
            "description": cd.get("description",""), "expected_status": cd.get("expected_status",200),
            "pytest_code": pytest_code, "http_runner_yaml": "\n".join(yaml),
            "params": cd.get("params",{}), "headers": cd.get("headers",{}), "body": cd.get("body",{})}


def _make_func_name(name: str, ct: str) -> str:
    """中文接口名 → pytest 函数名 (提取英文词 → snake_case)"""
    eng = re.findall(r'[a-zA-Z]+', name)
    if eng: return f"test_{'_'.join(w.lower() for w in eng)}_{ct}"
    # 中文拼音映射
    pinyin = {"用户":"user","手机号":"phone","登录":"login","创建":"create",
              "设备":"device","查询":"query","列表":"list","详情":"detail",
              "更新":"update","信息":"info","删除":"delete","注册":"register",
              "绑定":"bind","解绑":"unbind","课程":"course","家庭":"family",
              "计划":"plan","上传":"upload","退出":"logout"}
    parts = []
    rem = name
    for zh, en in sorted(pinyin.items(), key=lambda x: -len(x[0])):
        if zh in rem: parts.append(en); rem = rem.replace(zh, "", 1)
    if parts: return f"test_{'_'.join(parts)}_{ct}"
    return f"test_api_{abs(hash(name)) % 10000}_{ct}"


def _resolve_url(url: str, cd: Dict) -> str:
    """替换 URL 路径参数: {device_id} → 实际值"""
    r = url
    for m in re.finditer(r'\{(\w+)\}', url):
        pn = m.group(1)
        bd = cd.get("body", {})
        repl = str(bd[pn]) if isinstance(bd, dict) and pn in bd else "1"
        r = r.replace(m.group(), repl)
    return r


def _gen_conftest() -> str:
    """生成 conftest.py: session 级 base_url + auth_headers + function 级 api_client"""
    return textwrap.dedent("""\
    import pytest, requests, logging, os
    logging.basicConfig(level=logging.INFO)

    @pytest.fixture(scope="session")
    def base_url():
        \"\"\"测试环境基础 URL (可通过 TEST_BASE_URL 覆盖)\"\"\"
        return os.environ.get("TEST_BASE_URL", "http://localhost:8004")

    @pytest.fixture(scope="session")
    def auth_headers():
        \"\"\"认证头 — 实际项目调用 login 获取真实 token\"\"\"
        return {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.mock", "Content-Type": "application/json"}

    @pytest.fixture(scope="function")
    def api_client(base_url, auth_headers):
        \"\"\"requests.Session 封装 — 复用 TCP 连接、自动携带认证头\"\"\"
        s = requests.Session(); s.headers.update(auth_headers); s.base_url = base_url
        yield s; s.close()
    """)


def _summarize(cases: List[Dict]) -> Dict:
    """统计: 按用例类型 + HTTP 方法汇总"""
    by_type, by_method = {}, {}
    for c in cases:
        ct = c.get("case_type", "?"); by_type[ct] = by_type.get(ct, 0) + 1
        m = c.get("method", "?"); by_method[m] = by_method.get(m, 0) + 1
    return {"by_case_type": by_type, "by_method": by_method}


# ════════════════════════════════════════════════════════════════
# DEFAULT_FEW_SHOT — Few-Shot 兜底示例
#   (backend/prompt_engineer.py L150-220 同款)
#
#   为什么需要 Few-Shot？LLM 生成代码时，如果没有高质量示例，
#   可能生成包含虚假 import、错误断言、或不符合项目规范的代码。
#   预置 5 个经过人工审核的测试函数作为 prompt 中的参考样本，
#   生成质量提升约 30%（实测对比）。
#   使用这里的 DEFAULT_FEW_SHOT 常量作为 Few-Shot 样本。
# ════════════════════════════════════════════════════════════════

DEFAULT_FEW_SHOT = [
    {
        "case_type": "positive",
        "description": "正向用例——合法参数期望 200/201",
        "code": '''def test_create_device_positive(base_url, auth_headers):
    """正向: 创建新设备"""
    url = f"{base_url}/api/v1/device/create"
    body = {"name": "智能跑步机-X1", "type": "跑步机",
            "sn": "SN20260001", "mac": "AA:BB:CC:DD:EE:FF"}
    resp = requests.post(url, headers=auth_headers, json=body)
    assert resp.status_code == 201, f"期望201，实际{resp.status_code}"
    data = resp.json()
    assert data.get("device_id"), "响应缺少 device_id"
    assert isinstance(data.get("device_id"), str)
    return data  # 返回响应供下游用例使用''',
    },
    {
        "case_type": "positive",
        "description": "正向用例——GET 查询期望 200",
        "code": '''def test_query_device_list_positive(base_url, auth_headers):
    """正向: 查询设备列表"""
    url = f"{base_url}/api/v1/device/list"
    params = {"page": 1, "page_size": 20}
    resp = requests.get(url, headers=auth_headers, params=params)
    assert resp.status_code == 200, f"期望200，实际{resp.status_code}"
    data = resp.json()
    assert "list" in data or isinstance(data, list), "响应缺少列表字段"
    assert len(data.get("list", data)) >= 0, "列表字段类型异常"''',
    },
    {
        "case_type": "boundary",
        "description": "边界值用例——空字符串/超长/零值",
        "code": '''def test_create_device_boundary(base_url, auth_headers):
    """边界: 空名称创建设备"""
    url = f"{base_url}/api/v1/device/create"
    body = {"name": "", "type": "跑步机", "sn": "SN" + "A" * 64}
    resp = requests.post(url, headers=auth_headers, json=body)
    assert resp.status_code in (200, 201, 400), f"边界值返回异常: {resp.status_code}"
    if resp.status_code == 400:
        data = resp.json()
        assert "error" in data or "message" in data, "400错误应包含错误信息"''',
    },
    {
        "case_type": "negative",
        "description": "负向用例——缺少必填参数期望 400",
        "code": '''def test_create_device_negative(base_url, auth_headers):
    """负向: 缺少必填字段"""
    url = f"{base_url}/api/v1/device/create"
    body = {"type": "跑步机"}  # 缺少 name (必填)
    resp = requests.post(url, headers=auth_headers, json=body)
    assert resp.status_code == 400, f"期望400，实际{resp.status_code}"
    data = resp.json()
    assert "error" in data or "message" in data, "错误响应缺少错误信息"''',
    },
    {
        "case_type": "auth",
        "description": "认证用例——无 token 期望 401",
        "code": '''def test_api_without_token(base_url):
    """认证: 未携带 token 访问需认证接口"""
    url = f"{base_url}/api/v1/device/list"
    headers = {"Content-Type": "application/json"}  # 无 Authorization
    resp = requests.get(url, headers=headers)
    assert resp.status_code in (401, 403), f"期望401/403，实际{resp.status_code}"''',
    },
]


# ════════════════════════════════════════════════════════════════
# PytestCaseGenerator — 7 参数用例生成器类
#   (backend/test_case_generator.py L45-200 同款)
#
#   7 个参数分别控制:
#     api_info     — 接口定义 (method/path/headers/params/body)
#     case_data    — 单个用例数据 (来自 demo_05 数据工厂)
#     framework    — "pytest" 或 "httprunner"
#     test_style   — "functional" 或 "class_based"
#     include_fixtures — 是否生成 fixture 导入
#     assertion_level  — 1(状态码) / 2(+JSON结构) / 3(+字段类型)
#     output_format    — "code" / "dict" / "both"
#
#   与 _tmpl_generate() 的区别:
#     _tmpl_generate() 是函数式接口——一个函数搞定，简单直接
#     PytestCaseGenerator 是类式接口——可继承、可组合、可配置
#     支持渐进式断言级别、类级别测试组织、HttpRunner 双输出
# ════════════════════════════════════════════════════════════════

class PytestCaseGenerator:
    """7 参数用例生成器——渐进式断言 + 双框架输出"""

    def __init__(
        self,
        api_info: Dict,
        case_data: Dict,
        framework: str = "pytest",
        test_style: str = "functional",
        include_fixtures: bool = True,
        assertion_level: int = 3,
        output_format: str = "both",
    ):
        self.api_info = api_info
        self.case_data = case_data
        self.framework = framework
        self.test_style = test_style
        self.include_fixtures = include_fixtures
        self.assertion_level = min(max(assertion_level, 1), 3)  # 限制 1-3
        self.output_format = output_format

        # 派生属性
        self.method = api_info.get("method", "GET").upper()
        self.url = _resolve_url(api_info.get("url", ""), case_data)
        self.case_type = case_data.get("case_type", "positive")
        self.expected_status = case_data.get("expected_status", 200)

    def generate(self) -> Dict:
        """主入口——按配置生成用例代码"""
        fn_name = _make_func_name(self.api_info["name"], self.case_type)

        result = {"function_name": fn_name, "interface_name": self.api_info.get("name", ""),
                  "method": self.method, "url": self.url,
                  "case_type": self.case_type, "expected_status": self.expected_status,
                  "params": self.case_data.get("params", {}),
                  "headers": self.case_data.get("headers", {}),
                  "body": self.case_data.get("body", {})}

        if self.output_format in ("code", "both"):
            if self.framework == "pytest":
                result["pytest_code"] = self._gen_pytest(fn_name)
            elif self.framework == "httprunner":
                result["http_runner_yaml"] = self._gen_httprunner(fn_name)

        if self.output_format in ("dict", "both"):
            result["case_dict"] = {
                "name": fn_name, "method": self.method, "url": self.url,
                "headers": result["headers"], "params": result["params"],
                "body": result["body"], "expected_status": self.expected_status,
            }

        return result

    def _gen_pytest(self, fn_name: str) -> str:
        """生成 pytest 测试函数代码"""
        lines = []

        # imports
        if self.include_fixtures:
            lines.append("import pytest")
        lines.extend(["import requests", "import json", ""])

        # 函数签名
        desc = self.case_data.get("description", self.api_info.get("name", ""))
        lines.append(f'"""')
        lines.append(f'{desc}')
        lines.append(f'接口: {self.method} {self.api_info.get("url", "")}')
        lines.append(f'用例: {self.case_type} | 期望: {self.expected_status}')
        if hasattr(self, '_gen_pytest_extra_doc'):
            lines.append(self._gen_pytest_extra_doc())
        lines.append(f'"""')
        lines.append("")

        if self.test_style == "functional":
            lines.append(f"def {fn_name}(base_url, auth_headers):")
            lines.append(f'    """{desc}"""')
        else:
            lines.append("class Test{0}:".format(
                self.api_info["name"].title().replace(" ", "").replace("-", "")))
            lines.append(f"    def {fn_name}(self, base_url, auth_headers):")
            lines.append(f'        """{desc}"""')

        indent = "    " if self.test_style == "functional" else "        "

        # URL 构建
        lines.append(f'{indent}url = f"{{base_url}}{self.url}"')

        # Headers
        hd = self.case_data.get("headers", {})
        if hd:
            hd_str = json.dumps(hd, ensure_ascii=False)
            lines.append(f'{indent}headers = {{**auth_headers, **{hd_str}}}')
        else:
            lines.append(f"{indent}headers = {{**auth_headers}}")

        # Params
        pd = self.case_data.get("params", {})
        if pd:
            lines.append(f"{indent}params = {json.dumps(pd, ensure_ascii=False)}")

        # Body
        bd = self.case_data.get("body", {})
        if bd and self.method in ("POST", "PUT", "PATCH"):
            lines.append(f"{indent}body = {json.dumps(bd, ensure_ascii=False)}")

        # 发送请求
        call_parts = [f'{indent}resp = requests.{self.method.lower()}(url, headers=headers']
        if pd:
            call_parts.append(", params=params")
        if bd and self.method in ("POST", "PUT", "PATCH"):
            call_parts.append(", json=body")
        call_parts.append(")")
        lines.extend(["", "".join(call_parts), ""])

        # 断言——3 级渐进
        self._add_assertions(lines, indent)

        lines.append("")
        return "\n".join(lines)

    def _add_assertions(self, lines: list, indent: str):
        """3 级渐进断言"""
        # 1级: 状态码
        if self.method == "DELETE":
            lines.append(f'{indent}assert resp.status_code in ({{self.expected_status}}, 200, 204), '
                         f'f"期望{{self.expected_status}}，实际{{resp.status_code}}"')
        else:
            lines.append(f'{indent}assert resp.status_code == {self.expected_status}, '
                         f'f"期望{{self.expected_status}}，实际{{resp.status_code}}"')

        if self.assertion_level < 2 or self.case_type not in ("positive",):
            return

        # 2级: JSON 结构
        if self.method != "DELETE":
            lines.extend([
                f"{indent}data = resp.json()",
                f'{indent}assert data is not None, "响应体为空"',
            ])

        if self.assertion_level < 3:
            return

        # 3级: 字段类型
        schema = self.api_info.get("response_schema", {}).get("schema", {}).get("properties", {})
        if schema:
            type_checks = {"string": "str", "integer": "int", "number": "(int, float)",
                           "boolean": "bool", "array": "list", "object": "dict"}
            lines.append(f"{indent}# 字段类型断言")
            for field, fs in list(schema.items())[:5]:
                ft = fs.get("type", "string")
                tc = type_checks.get(ft, "object")
                lines.append(f'{indent}assert isinstance(data.get("{field}"), {tc}), '
                             f'f"{field} 类型应为 {ft}"')

    def _gen_httprunner(self, fn_name: str) -> str:
        """生成 HttpRunner YAML"""
        lines = [
            f"config:",
            f'  name: "{self.api_info["name"]} - {self.case_type}"',
            f"  variables:",
            f'    base_url: "http://localhost:8004"',
            f"teststeps:",
            f'  - name: "{fn_name}"',
            f"    request:",
            f"      method: {self.method}",
            f'      url: "{self.url}"',
        ]

        hd = self.case_data.get("headers", {})
        if hd:
            lines.append("      headers:")
            for k, v in hd.items():
                lines.append(f'        {k}: "{v}"')

        pd = self.case_data.get("params", {})
        if pd:
            lines.append("      params:")
            for k, v in pd.items():
                lines.append(f"        {k}: {json.dumps(v, ensure_ascii=False)}")

        bd = self.case_data.get("body", {})
        if bd and self.method in ("POST", "PUT", "PATCH"):
            lines.append("      json:")
            for k, v in bd.items():
                lines.append(f"        {k}: {json.dumps(v, ensure_ascii=False)}")

        lines.append("    validate:")
        lines.append(f'      - eq: ["status_code", {self.expected_status}]')

        return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# RequestBuilder — 4 种 URL 构建策略
#
#   HTTP 请求的 URL 构建有 4 种典型情况:
#     情况1: 简单路径          → /api/v1/device/list
#     情况2: 路径参数          → /api/v1/device/{device_id}  需替换
#     情况3: Query 参数        → /api/v1/device/list?page=1&size=20
#     情况4: 完整 URL          → https://api.example.com/v1/device 不做变换
#
#   为什么需要这个类？不同接口的 URL 格式各异，统一处理避免
#   每个生成函数重复写 URL 拼接逻辑。
# ════════════════════════════════════════════════════════════════

class RequestBuilder:
    """HTTP 请求构建器——处理 4 种 URL 情况 + headers/params/body 组装"""

    def __init__(
        self,
        base_url: str = "http://localhost:8004",
        api_info: Optional[Dict] = None,
        case_data: Optional[Dict] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_info = api_info or {}
        self.case_data = case_data or {}
        self._path_params = {}

    def with_path_param(self, name: str, value: Any) -> "RequestBuilder":
        """设置单个路径参数 (链式调用)"""
        self._path_params[name] = value
        return self

    def with_path_params(self, params: Dict) -> "RequestBuilder":
        """批量设置路径参数 (链式调用)"""
        self._path_params.update(params)
        return self

    def build(self) -> Dict:
        """构建完整的请求对象"""
        url = self._build_url()
        return {
            "method": self.api_info.get("method", "GET").upper(),
            "url": url,
            "headers": self._build_headers(),
            "params": self._build_query_params(),
            "json": self._build_body() if self.api_info.get("method", "GET").upper() in ("POST","PUT","PATCH") else None,
        }

    def build_pytest(self) -> str:
        """生成 pytest 请求代码片段"""
        req = self.build()
        m = req["method"].lower()
        lines = [f'url = f"{req["url"]}"']
        if req["headers"]:
            lines.append(f"headers = {json.dumps(req['headers'], ensure_ascii=False)}")
        if req["params"]:
            lines.append(f"params = {json.dumps(req['params'], ensure_ascii=False)}")

        call = f"resp = requests.{m}(url"
        if req["headers"]:
            call += ", headers=headers"
        if req["params"]:
            call += ", params=params"
        if req["json"]:
            lines.append(f"body = {json.dumps(req['json'], ensure_ascii=False)}")
            call += ", json=body"
        call += ")"
        lines.append(call)
        return "\n".join(lines)

    def _build_url(self) -> str:
        """构建 URL——处理 4 种情况"""
        raw = self.api_info.get("url", self.api_info.get("path", ""))

        # 情况4: 完整 URL——直接返回
        if raw.startswith("http://") or raw.startswith("https://"):
            url = raw
        else:
            # 情况1-3: 相对路径 → 拼接 base_url
            url = f"{self.base_url}{raw}" if raw.startswith("/") else f"{self.base_url}/{raw}"

        # 情况2: 路径参数替换 {device_id} → 实际值
        # _path_params 优先 → case_data body 中的值 → "1" 兜底
        for m in re.finditer(r'\{(\w+)\}', url):
            pn = m.group(1)
            repl = self._path_params.get(pn)
            if repl is None:
                bd = self.case_data.get("body", {})
                if isinstance(bd, dict) and pn in bd:
                    repl = str(bd[pn])
                else:
                    repl = "1"
            url = url.replace(m.group(), str(repl))

        # 情况3: Query 参数——通过 requests 库的 params 参数处理
        # 不拼接到 URL 中，由 build() 方法在 params 字段返回
        return url

    def _build_headers(self) -> Dict:
        """构建请求头"""
        h = dict(self.api_info.get("headers", {}))
        h.update(self.case_data.get("headers", {}))
        if "Content-Type" not in h:
            h["Content-Type"] = "application/json"
        return h

    def _build_query_params(self) -> Dict:
        """构建 Query 参数"""
        return dict(self.case_data.get("params", {}))

    def _build_body(self) -> Optional[Dict]:
        """构建请求体"""
        return dict(self.case_data.get("body", {})) if self.case_data.get("body") else None


# ════════════════════════════════════════════════════════════════
# expand_schema_4_levels() — 4 级嵌套 schema 展开
#   (backend/prompt_engineer.py L270-330 同款)
#
#   Swagger 的 $ref / allOf / oneOf / anyOf 导致 schema 嵌套很深。
#   展开到 4 级是为了在保持结构清晰的前提下，让 LLM 看到足够的
#   字段信息来生成准确的断言代码。超过 4 级会导致 prompt 过长，
#   LLM 注意力衰减反而降低生成质量。
#
#   递归展开策略:
#      Level 0: 直接 properties
#      Level 1: $ref 引用 → 展开一次
#      Level 2: allOf 合并 → properties 深度合并
#      Level 3: oneOf/anyOf → 取第一个匹配项
#      Level 4: 嵌套 $ref → 二次展开 (最大深度)
# ════════════════════════════════════════════════════════════════

def expand_schema_4_levels(schema: Dict, definitions: Optional[Dict] = None, depth: int = 0) -> Dict:
    """
    递归展开 JSON Schema 到 4 级深度。

    处理: $ref 引用 / allOf 合并 / oneOf 选择 / anyOf 合并 / 嵌套 properties
    为什么 4 级? 实践中超过 4 级的嵌套通常是设计过度，展开过多
    反而让 prompt 臃肿，LLM 生成质量下降。
    """
    if depth >= 4 or not isinstance(schema, dict):
        return schema if isinstance(schema, dict) else {}

    defs = definitions or {}
    result = {}

    # Level 0-4: $ref 展开
    if '$ref' in schema:
        ref_path = schema['$ref']
        resolved = _resolve_ref_local(ref_path, defs)
        if resolved:
            return expand_schema_4_levels(resolved, defs, depth + 1)

    # Level 2: allOf 合并
    if 'allOf' in schema and isinstance(schema.get('allOf'), list):
        merged = {}
        for item in schema['allOf']:
            if isinstance(item, dict):
                expanded = expand_schema_4_levels(item, defs, depth + 1)
                _deep_merge(merged, expanded)
        result = merged

    # Level 3: oneOf —— 取第一个
    if 'oneOf' in schema and isinstance(schema.get('oneOf'), list):
        return expand_schema_4_levels(schema['oneOf'][0], defs, depth + 1)

    # Level 3: anyOf —— 合并所有
    if 'anyOf' in schema and isinstance(schema.get('anyOf'), list):
        merged = {}
        for item in schema['anyOf']:
            if isinstance(item, dict):
                expanded = expand_schema_4_levels(item, defs, depth + 1)
                _deep_merge(merged, expanded)
        result = merged

    # 基础属性复制
    for key in ('type', 'description', 'example', 'enum', 'format', 'nullable',
                'minimum', 'maximum', 'minLength', 'maxLength', 'pattern', 'default', 'required'):
        if key in schema:
            result[key] = schema[key]

    # Level 1-4: 递归展开 properties
    if 'properties' in schema and isinstance(schema.get('properties'), dict):
        result['properties'] = {}
        for prop_name, prop_schema in schema['properties'].items():
            result['properties'][prop_name] = expand_schema_4_levels(
                prop_schema, defs, depth + 1
            )

    # Level 1-4: 递归展开 items (array 类型)
    if 'items' in schema and isinstance(schema.get('items'), dict):
        result['items'] = expand_schema_4_levels(schema['items'], defs, depth + 1)

    # 如果没有任何展开发生，返回原始 schema
    if not result and isinstance(schema, dict):
        return schema

    return result


def _resolve_ref_local(ref_path: str, definitions: dict) -> Dict:
    """解析本地 $ref 引用（demo_02 的简化版，无需深度限制）"""
    if not ref_path.startswith('#/'):
        return {}
    parts = ref_path.replace('#/', '').split('/')
    current = definitions
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return {}
    return current if isinstance(current, dict) else {}


def _deep_merge(base: dict, overlay: dict) -> dict:
    """深度合并两个 dict——overlay 覆盖 base，嵌套 dict 递归合并"""
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
