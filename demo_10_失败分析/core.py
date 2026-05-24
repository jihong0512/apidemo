"""
demo_10 失败分析 -- 核心逻辑 (重构版 v3.0)
════════════════════════════════════════════════════════════════
对应课件: 第10讲 智能断言与失败分析 -- AI驱动的结果诊断
后端源码参考:
  - backend/app/services/failure_analyzer.py (377行) -- 双路径失败分析
  - backend/app/services/ai_suggestion_service.py (295行) -- AI优化建议生成

双路径架构 (from failure_analyzer.py L20-121):
  主路径: LLM精准分析
    收集4类证据 (请求/响应/断言/错误信息) → 组装Prompt → LLM诊断
    → 提取JSON结果 → 含root_cause + fix_suggestions + prevention_measures

  兜底路径: 规则分类 (7条if/elif规则, 100%可靠)
    status_code=0    → network_error     (网络不可达)
    status_code=404  → interface_error    (接口不存在)
    status_code=401  → authentication_error (认证失败)
    status_code=403  → authorization_error  (权限不足)
    status_code=500  → server_error       (服务端内部错误)
    status_code 200-299 but assertions fail → assertion_error
    "timeout" in error → timeout_error

三层分类:
  L1 环境问题 (environment):  网络超时 / 服务不可用 / DNS解析失败
  L2 数据问题 (data):        Token过期 / 资源不存在 / 依赖数据不对
  L3 代码Bug (code_bug):     断言逻辑错误 / 接口Schema变更 / 业务逻辑变更

为什么双路径?
  - LLM 分析更精准 (能识别"看起来像数据问题但实际是接口Schema变更")
  - 但 LLM 可能不稳定 (同样输入不同输出) → 规则兜底保证100%可用
  - 规则分析对于标准HTTP错误码 (404/500等) 非常可靠

为什么需要三层分类?
  - 不同类别对应不同的修复责任人: 环境→运维, 数据→测试, Bug→开发
  - P0/P1/P2 优先级排序帮助团队高效分配修复资源
  - 分布统计帮助识别"系统性问题" (如80%失败是数据问题→检查数据工厂)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import (
    add_project_to_sys_path, Config, llm_client, read_json_safe,
)
add_project_to_sys_path(__file__)

import json
from collections import defaultdict
from typing import List, Dict, Any, Optional


# ══════════════════════════════════════════════════════════════
# 三层分类映射 & 置信度基准
# 对应 failure_analyzer.py L136-270 的7条规则
# ══════════════════════════════════════════════════════════════

# L1 → L3 的分类映射 (from failure_analyzer.py L154-268)
CATEGORY_RULES = {
    0:    ("environment",       "network_error",     0.75),
    404:  ("data",              "interface_error",    0.85),
    401:  ("data",              "authentication_error", 0.88),
    403:  ("data",              "authorization_error", 0.82),
    500:  ("code_bug",          "server_error",       0.80),
    502:  ("environment",       "gateway_error",      0.78),
    503:  ("environment",       "service_unavailable", 0.80),
}

# 中文分类名
CATEGORY_CN = {
    "environment": "环境问题",
    "data": "数据问题",
    "code_bug": "代码Bug",
    "unknown": "未分类",
}

# 子类型中文名
SUBCATEGORY_CN = {
    "network_error": "网络错误",
    "interface_error": "接口不存在",
    "authentication_error": "认证失败",
    "authorization_error": "权限不足",
    "server_error": "服务端错误",
    "gateway_error": "网关错误",
    "service_unavailable": "服务不可用",
    "assertion_error": "断言失败",
    "timeout_error": "请求超时",
    "unknown": "未知错误",
}


# ══════════════════════════════════════════════════════════════
# 路径2: 规则分类 (兜底路径, 100%可靠)
# 对应 failure_analyzer.py L136-270 _rule_based_analysis()
#
# 7条规则按优先级匹配, 命中即返回
# 为什么顺序很重要?
#   先检查 error_message (直接标记timeout), 再检查 status_code
#   因为 status_code=0 + error包含timeout → 应先识别为timeout, 而非network_error
# ══════════════════════════════════════════════════════════════

def _rule_based_analysis(
    response_data: Optional[Dict[str, Any]],
    assertions: List[Dict[str, Any]],
    error_message: str,
) -> Dict[str, Any]:
    """
    规则分类 (from failure_analyzer.py L136-268)

    7条if/elif规则, 按优先级匹配:
      检查顺序: error_message → status_code → assertions
    """
    status_code = (response_data or {}).get("status_code", 0)
    body = (response_data or {}).get("body", {})

    # ── 规则1: 检查 error_message 中的关键字 ──
    if error_message:
        error_lower = error_message.lower()
        if "timeout" in error_lower or "timed out" in error_lower:
            return _build_rule_result(
                "environment", "timeout_error",
                failure_reason="请求超时: 服务器未在规定时间内响应",
                root_cause="网络延迟过高或服务端处理超时",
                fix_suggestions=[
                    "增加请求超时时间 (当前30s → 60s)",
                    "检查服务端负载和响应时间",
                    "检查网络连通性和防火墙规则",
                ],
                prevention_measures=[
                    "设置合理的超时时间",
                    "添加接口响应时间监控",
                    "对慢接口实施异步处理",
                ],
                confidence=0.82,
            )
        if "connection" in error_lower or "refused" in error_lower:
            return _build_rule_result(
                "environment", "network_error",
                failure_reason="网络连接失败: 目标服务器不可达",
                root_cause="服务器未启动或端口被防火墙拦截",
                fix_suggestions=[
                    "检查服务器是否正常运行",
                    "验证URL和端口号是否正确",
                    "检查防火墙规则",
                ],
                prevention_measures=[
                    "添加健康检查端点",
                    "实现服务自动重启",
                    "部署监控告警",
                ],
                confidence=0.80,
            )

    # ── 规则2-8: 按HTTP状态码分类 ──
    if status_code == 0:
        return _build_rule_result(
            "environment", "network_error",
            failure_reason="网络连接失败: 无法连接到服务器 (status_code=0)",
            root_cause="服务器未启动、网络不通或DNS解析失败",
            fix_suggestions=[
                "检查目标服务器是否运行",
                "使用 curl 或 Postman 手动验证接口可达性",
                "检查DNS配置或使用IP直连",
            ],
            prevention_measures=[
                "添加接口可用性预检",
                "设置连接超时时间",
                "实现重试机制 (max_retries=3)",
            ],
            confidence=0.75,
        )

    elif status_code == 404:
        return _build_rule_result(
            "data", "interface_error",
            failure_reason="资源不存在 (404 Not Found): 接口URL或资源ID无效",
            root_cause="URL路径错误、资源已被删除或接口版本已更新",
            fix_suggestions=[
                "检查URL路径是否与接口文档一致",
                "验证资源ID是否有效 (可能已被前置用例删除)",
                "检查接口版本号和路由注册",
            ],
            prevention_measures=[
                "用例执行前验证依赖资源存在",
                "定期同步接口文档",
                "使用接口版本管理",
            ],
            confidence=0.85,
        )

    elif status_code == 401:
        return _build_rule_result(
            "data", "authentication_error",
            failure_reason="认证失败 (401 Unauthorized): Token无效或过期",
            root_cause="Token缺失、格式错误或已过期",
            fix_suggestions=[
                "确保登录用例在所有业务用例之前执行",
                "检查Token是否正确注入到Authorization请求头",
                "验证Token的过期时间设置",
                "检查Token生成逻辑 (算法/密钥/载荷)",
            ],
            prevention_measures=[
                "实现Token自动刷新机制",
                "添加Token有效性预检",
                "用例编排中明确token传递链路",
            ],
            confidence=0.88,
        )

    elif status_code == 403:
        return _build_rule_result(
            "data", "authorization_error",
            failure_reason="权限不足 (403 Forbidden): 用户无权访问此资源",
            root_cause="当前用户角色/权限不满足接口访问要求",
            fix_suggestions=[
                "检查测试账号的角色权限配置",
                "确认接口的权限要求 (是否需要管理员角色)",
                "验证请求参数中是否缺少权限标识",
            ],
            prevention_measures=[
                "测试前验证账号权限",
                "为不同角色准备独立的测试账号",
                "添加权限校验断言",
            ],
            confidence=0.82,
        )

    elif status_code == 500:
        return _build_rule_result(
            "code_bug", "server_error",
            failure_reason="服务端内部错误 (500 Internal Server Error)",
            root_cause="服务端代码抛出异常或数据库操作失败",
            fix_suggestions=[
                "查看服务端错误日志, 定位异常堆栈",
                "检查测试数据是否触发了未处理的边界条件",
                "验证数据库连接和表结构",
                "排查接口代码中的异常处理是否完善",
            ],
            prevention_measures=[
                "完善服务端异常处理和日志记录",
                "添加接口级别的错误监控",
                "测试环境部署Sentinel/Error Tracking",
            ],
            confidence=0.80,
        )

    elif status_code == 502 or status_code == 503:
        return _build_rule_result(
            "environment", "gateway_error" if status_code == 502 else "service_unavailable",
            failure_reason=f"网关/服务异常 ({status_code}): 上游服务不可用",
            root_cause="反向代理或API网关无法连接到后端服务",
            fix_suggestions=[
                "检查后端服务是否正常运行",
                "验证API网关配置 (upstream地址)",
                "检查服务注册和发现状态",
            ],
            prevention_measures=[
                "部署服务健康检查和自动恢复",
                "配置网关重试策略",
                "添加服务可用性监控",
            ],
            confidence=0.78,
        )

    elif 200 <= status_code < 300:
        # ── 规则: 状态码正常但断言失败 ──
        failed_assertions = [a for a in assertions if not a.get("passed", False)]
        if failed_assertions:
            # 分析具体的断言失败类型
            assertion_details = []
            for fa in failed_assertions[:5]:
                atype = fa.get("type", "unknown")
                expected = fa.get("expected", "")
                actual = fa.get("actual", "")
                assertion_details.append(f"{atype}: 期望={expected}, 实际={actual}")

            return _build_rule_result(
                "code_bug", "assertion_error",
                failure_reason=f"断言失败 ({len(failed_assertions)}/{len(assertions)}个断言未通过): "
                              + "; ".join(assertion_details[:3]),
                root_cause="响应数据格式/内容与期望不一致, 可能是接口Schema变更",
                fix_suggestions=[
                    "对比接口文档, 检查响应字段是否有变更",
                    "用Postman手动调接口, 对比实际响应结构",
                    "更新断言中的expected值以匹配新的响应格式",
                    "检查业务逻辑是否发生了变化 (如字段改名/类型变更)",
                ],
                prevention_measures=[
                    "定期检查接口Schema变更",
                    "使用JSON Schema验证替代精确值断言",
                    "断言失败时自动保存actual值供对比",
                ],
                confidence=0.90,
            )

    # ── 兜底规则 ──
    return _build_rule_result(
        "code_bug", "unknown",
        failure_reason=f"未分类错误: HTTP {status_code}",
        root_cause="需要人工排查: 状态码不在常见分类中",
        fix_suggestions=[
            "检查接口响应体中的错误信息",
            "查看服务端和应用日志",
            "手动复现相同请求确认问题",
        ],
        prevention_measures=[
            "扩展错误分类规则覆盖更多场景",
            "完善日志记录",
        ],
        confidence=0.50,
    )


def _build_rule_result(
    category: str,
    subcategory: str,
    failure_reason: str,
    root_cause: str,
    fix_suggestions: List[str],
    prevention_measures: List[str],
    confidence: float,
) -> Dict[str, Any]:
    """构建规则分析结果"""
    return {
        "category": category,
        "category_cn": CATEGORY_CN.get(category, "未分类"),
        "subcategory": subcategory,
        "subcategory_cn": SUBCATEGORY_CN.get(subcategory, "未知"),
        "failure_reason": failure_reason,
        "root_cause": root_cause,
        "fix_suggestions": fix_suggestions,
        "prevention_measures": prevention_measures,
        "confidence": confidence,
        "analysis_mode": "rule_based",
    }


# ══════════════════════════════════════════════════════════════
# 路径1: LLM 精准分析 (主路径)
# 对应 failure_analyzer.py L48-85 的 LLM Prompt
#
# 为什么收集4类证据?
#   1. request_data  → 确认"发了什么请求" (URL/参数/Headers是否正确)
#   2. response_data → 确认"服务器回了什么" (状态码/响应体)
#   3. assertions    → 确认"哪些断言没过" (期望vs实际)
#   4. error_message → 确认"系统报了什么错" (网络/认证/解析错误)
#   只有4类证据齐备, LLM才能做精准的根因定位
# ══════════════════════════════════════════════════════════════

def _llm_analyze_single(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    LLM精准分析 (from failure_analyzer.py L48-85)

    组装4类证据 → Prompt → LLM诊断 → 提取JSON结果

    Returns:
        Dict if success, None if LLM unavailable/failed
    """
    if not Config.is_llm_available():
        return None

    # ── 收集4类证据 ──
    case_name = result.get("test_case_id", "unknown")
    case_type = result.get("case_type", "")
    interface_name = result.get("interface", "")

    request_data = result.get("request_data", {})
    response_data = result.get("response_data", {})
    if response_data is None:
        response_data = {}

    assertions = result.get("assertions", [])
    error_message = result.get("error_message", result.get("error", ""))

    # 简化响应体: 避免超过LLM上下文窗口
    response_body = response_data.get("body", {})
    if isinstance(response_body, dict):
        # 截断大响应体 (保留前500字符)
        body_str = json.dumps(response_body, ensure_ascii=False)
        if len(body_str) > 500:
            body_str = body_str[:500] + "...(truncated)"
    else:
        body_str = str(response_body)[:500]

    # 提取断言失败信息
    failed_assertions = [a for a in assertions if not a.get("passed", False)]
    assertions_summary = json.dumps(failed_assertions[:5], ensure_ascii=False)

    # ── 组装 Prompt (from failure_analyzer.py L48-85) ──
    prompt = f"""请分析以下API测试失败的原因，并提供详细的失败分析和改进建议。

测试用例信息：
- 用例名称：{case_name}
- 接口：{interface_name}
- 用例类型：{case_type}

请求信息：
- 方法：{request_data.get('method', '')}
- URL：{request_data.get('url', '')}
- 请求头：{json.dumps(request_data.get('headers', {}), ensure_ascii=False)}
- 请求体：{json.dumps(request_data.get('body', {}), ensure_ascii=False)}

响应信息：
- 状态码：{response_data.get('status_code', 'N/A')}
- 响应体：{body_str}

断言结果：
{assertions_summary}

错误信息：
{error_message}

请提供：
1. 失败原因分析（详细说明为什么会失败）
2. 可能的原因分类（environment/data/code_bug）
3. 根本原因
4. 修复建议（至少3条具体的修复步骤）
5. 预防措施（如何避免类似问题再次发生）

请以JSON格式返回，格式如下：
{{
    "failure_reason": "失败原因详细说明",
    "category": "environment|data|code_bug",
    "root_cause": "根本原因",
    "fix_suggestions": ["建议1", "建议2", "建议3"],
    "prevention_measures": ["措施1", "措施2"]
}}
"""
    try:
        llm_result = llm_client.extract_json(prompt, temperature=0.3, max_tokens=1500)
        if llm_result and "failure_reason" in llm_result:
            # 标准化字段名
            llm_result["analysis_mode"] = "ai_analyzed"
            llm_result["confidence"] = _estimate_confidence(llm_result.get("category", "unknown"))
            return llm_result
        return None
    except Exception as e:
        print(f"  [WARN] LLM分析失败: {e}")
        return None


def _estimate_confidence(category: str) -> float:
    """估算LLM分析置信度"""
    base = {
        "environment": 0.72,
        "data": 0.85,
        "code_bug": 0.88,
        "network_error": 0.75,
        "interface_error": 0.85,
        "authentication_error": 0.88,
        "authorization_error": 0.82,
        "server_error": 0.80,
        "assertion_error": 0.90,
        "timeout_error": 0.82,
    }
    return base.get(category, 0.70)


# ══════════════════════════════════════════════════════════════
# 单用例分析: 双路径编排
# 对应 failure_analyzer.py L20-121 analyze_failure()
# ══════════════════════════════════════════════════════════════

def _analyze_single(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    分析单个用例失败原因 (双路径架构)

    路径1 (主): LLM精准分析 → 提取JSON结果
    路径2 (兜底): 规则分类 → 7条if/elif规则

    这个双路径模式来自 failure_analyzer.py L20-121:
      try LLM → if failed → fallback to rule_based
    """
    # .get() 仅当key不存在时返回默认值; key存在但值为None时返回None
    # 使用 or {} 兜底: None 和 {} 都转为 {}
    response_data = result.get("response_data") or {}
    assertions = result.get("assertions") or []
    error_message = result.get("error_message", result.get("error", ""))

    # ── 路径1: LLM分析 ──
    llm_result = _llm_analyze_single(result)
    if llm_result:
        # 从 fix_suggestions 列表提取首条作为 suggested_fix (向后兼容entry文件)
        fix_list = llm_result.get("fix_suggestions", [])
        suggested_fix = fix_list[0] if fix_list else "需要人工排查"
        analysis = {
            "test_case": result.get("test_case_id", "unknown"),
            "interface": result.get("interface", ""),
            "case_type": result.get("case_type", ""),
            "status": result.get("status", ""),
            "expected_status": result.get("expected_status"),
            "actual_status": result.get("actual_status", response_data.get("status_code")),
            "suggested_fix": suggested_fix,
            **llm_result,
        }
        # ── 补全缺失字段 ──
        if "category_cn" not in analysis:
            category_en = analysis.get("category", "unknown")
            CATEGORY_CN_MAP = {
                "environment": "环境问题", "data": "数据问题",
                "code_bug": "代码Bug", "unknown": "未分类"
            }
            analysis["category_cn"] = CATEGORY_CN_MAP.get(category_en, "未分类")
        if "subcategory" not in analysis:
            analysis["subcategory"] = ""
        if "subcategory_cn" not in analysis:
            analysis["subcategory_cn"] = ""
        return analysis

    # ── 路径2: 规则兜底 ──
    rule_result = _rule_based_analysis(response_data, assertions, error_message)
    fix_list = rule_result.get("fix_suggestions", [])
    suggested_fix = fix_list[0] if fix_list else "需要人工排查"
    return {
        "test_case": result.get("test_case_id", "unknown"),
        "interface": result.get("interface", ""),
        "case_type": result.get("case_type", ""),
        "status": result.get("status", ""),
        "expected_status": result.get("expected_status"),
        "actual_status": result.get("actual_status", response_data.get("status_code") if response_data else None),
        "suggested_fix": suggested_fix,
        **rule_result,
    }


# ══════════════════════════════════════════════════════════════
# 核心: analyze_failures() -- 失败分析主函数
#
# 与 failure_analyzer.py L272-318 analyze_task_failures() 对应
# 但demo版简化: 直接用执行结果列表, 不需要查数据库
#
# 分析流水线:
#   1. 筛选失败用例 (status != "passed")
#   2. 对每个失败用例执行双路径分析
#   3. 汇总: 类别分布 + 状态码分布 + 优先修复建议
#   4. 生成summary统计数据
# ══════════════════════════════════════════════════════════════

def analyze_failures(execution_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    智能失败分析: 三层分类 + LLM根因诊断 + 修复建议

    双路径架构 (from failure_analyzer.py):
      主路径: LLM精准分析（收集4类证据 → 组装Prompt → LLM诊断）
      兜底路径: 规则分类（7条if/elif规则，100%可靠）

    三层分类:
      L1 环境问题: 网络超时/服务不可用
      L2 数据问题: Token过期/资源不存在
      L3 代码Bug: 断言错误/接口变更

    置信度评分 (为什么不同类别置信度不同?):
      - 环境问题 (0.70-0.82): 可能由多种原因导致, 需要更多上下文
      - 数据问题 (0.82-0.88): Token/状态码特征明显, 分类可信度高
      - 代码Bug (0.80-0.90): 断言失败的特征非常明确

    Args:
        execution_result: 执行结果 (from step_09_results.json)
    Returns:
        Dict: {
            analyses: [...],
            summary: {total, pass_rate, top_category, category_distribution, status_code_distribution},
            ai_enhanced: bool,
            # 向后兼容字段:
            analysis, total_failures, by_category, suggestions
        }
    """
    results = execution_result.get("results", [])
    total = len(results) if results else 0

    if not results:
        return {
            "analyses": [],
            "analysis": [],
            "total_failures": 0,
            "by_category": {},
            "summary": {
                "total": 0,
                "failures": 0,
                "pass_rate": 100.0,
                "top_category": None,
                "category_distribution": {},
                "status_code_distribution": {},
            },
            "suggestions": [],
            "ai_enhanced": False,
        }

    # ── 1. 筛选失败用例 ──
    failed = [r for r in results if r.get("status") != "passed"]
    passed = [r for r in results if r.get("status") == "passed"]

    # ── 2. 逐条分析 ──
    analyses = []
    ai_count = 0
    for f in failed:
        analysis = _analyze_single(f)
        analyses.append(analysis)
        if analysis.get("analysis_mode") == "ai_analyzed":
            ai_count += 1

    # ── 3. 汇总统计 ──
    # 类别分布
    category_distribution: Dict[str, int] = {}
    for a in analyses:
        cat = a.get("category", "unknown")
        category_distribution[cat] = category_distribution.get(cat, 0) + 1

    # 状态码分布
    status_code_distribution: Dict[str, int] = {}
    for r in failed:
        actual_status = r.get("actual_status", (r.get("response_data") or {}).get("status_code", "N/A"))
        status_code_distribution[str(actual_status)] = status_code_distribution.get(str(actual_status), 0) + 1

    # 顶级失败类别 (from failure_analyzer.py L316)
    top_category = max(category_distribution.items(), key=lambda x: x[1])[0] if category_distribution else None

    # 通过率
    pass_rate = round(len(passed) / total * 100, 1) if total > 0 else 0.0

    # ── 4. 生成优先修复建议 ──
    suggestions = _generate_suggestions(analyses, category_distribution)

    # ── 5. 向后兼容的 by_category (entry文件访问) ──
    by_category = {}
    for a in analyses:
        cat = a.get("category", "unknown")
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(a.get("test_case", "unknown"))

    # ── 6. 生成摘要文本 ──
    summary_text = _generate_summary_text(category_distribution, top_category, pass_rate, ai_count)

    # ── 组装详细统计字典 (step_10_analysis.json 的主体内容) ──
    summary_stats = {
        "total": total,
        "failures": len(failed),
        "pass_rate": pass_rate,
        "top_category": top_category,
        "top_category_cn": CATEGORY_CN.get(top_category, "未知") if top_category else None,
        "category_distribution": {CATEGORY_CN.get(k, k): v for k, v in category_distribution.items()},
        "status_code_distribution": status_code_distribution,
    }

    result = {
        # ── 新规范字段 ──
        "analyses": analyses,
        "summary": summary_stats,   # dict: {total, pass_rate, top_category, category_distribution, ...}
        "summary_text": summary_text,  # 人类可读的中文摘要
        "ai_enhanced": ai_count > 0,
        # ── 向后兼容字段 (entry文件使用) ──
        "analysis": analyses,       # entry遍历 result["analysis"], 每项含 category_cn/test_case/root_cause/suggested_fix
        "total_failures": len(failed),
        "by_category": {CATEGORY_CN.get(k, k): v for k, v in by_category.items()},
        "suggestions": suggestions,
    }

    # ── Schema 变更检测 ──
    schema_changes = []
    try:
        from demo_02_文档解析.core import parse_swagger_document
        current_schema = read_json_safe("step_02_interfaces.json")
        if current_schema:
            schema_changes = detect_schema_changes(current_schema, {})  # 与空Schema比较
    except Exception:
        pass

    # ── AI 增强建议 ──
    ai_suggestions = []
    if Config.is_llm_available() and analyses:
        try:
            suggestion_service = AISuggestionService(analyses[:5])
            ai_suggestions = suggestion_service.generate_suggestions()
        except Exception:
            pass

    # ── 将增强结果加入返回字典 ──
    if schema_changes:
        result["schema_changes"] = schema_changes
    if ai_suggestions:
        result["ai_suggestions"] = ai_suggestions

    return result


# ══════════════════════════════════════════════════════════════
# 修复建议生成 (P0/P1/P2优先级)
# 对应 failure_analyzer.py L272-318 + ai_suggestion_service.py
#
# 优先级定义:
#   P0 (立即修复): 影响整体通过率 > 30% 的问题
#   P1 (本迭代修复): 单类别占比 > 20% 的问题
#   P2 (下迭代优化): 偶发/低影响的问题
# ══════════════════════════════════════════════════════════════

def _generate_suggestions(
    analyses: List[Dict],
    category_distribution: Dict[str, int],
) -> List[Dict[str, Any]]:
    """生成优先级排序的修复建议"""
    total = len(analyses)
    suggestions = []

    # P0: 数据类问题 (通常占比最高, 且修复成本低)
    data_count = category_distribution.get("data", 0)
    if data_count > 0:
        suggestions.append({
            "priority": "P0" if data_count / total > 0.3 else "P1",
            "category": "数据问题",
            "action": "检查 Token 传递链路和测试数据有效性",
            "detail": (
                "数据类失败占比高通常说明token管理或资源依赖有问题。"
                "建议: ① 确保login用例在所有业务用例之前执行; "
                "② 检查Authorization头的Bearer token是否正确注入; "
                "③ 验证前置用例(创建资源)是否成功, 资源ID是否正确传递"
            ),
            "affected_cases": [a.get("test_case", "") for a in analyses if a.get("category") == "data"][:5],
        })

    # P1: 代码Bug (断言/接口变更)
    bug_count = category_distribution.get("code_bug", 0)
    if bug_count > 0:
        suggestions.append({
            "priority": "P1" if bug_count / total > 0.2 else "P2",
            "category": "代码Bug",
            "action": "核对接口文档, 更新断言逻辑",
            "detail": (
                "代码级失败可能是接口Schema变更或断言条件过严。"
                "建议: ① 对比接口文档的最新版本, 检查响应字段是否有变更; "
                "② 用Postman手动调接口确认实际响应结构; "
                "③ 优先使用JSON Schema验证代替精确值断言, 提高鲁棒性"
            ),
            "affected_cases": [a.get("test_case", "") for a in analyses if a.get("category") == "code_bug"][:5],
        })

    # P2: 环境问题 (需要运维介入)
    env_count = category_distribution.get("environment", 0)
    if env_count > 0:
        suggestions.append({
            "priority": "P2",
            "category": "环境问题",
            "action": "检查 MockResponder 和网络连通性",
            "detail": (
                "环境问题通常是间歇性的。"
                "建议: ① 确认 MockResponder 覆盖了所有用例场景的响应; "
                "② 检查是否有并发导致的竞态条件; "
                "③ 网络超时可尝试降低并发数(concurrency=3 → 1)"
            ),
            "affected_cases": [a.get("test_case", "") for a in analyses if a.get("category") == "environment"][:3],
        })

    # 如果没有建议, 添加通用建议
    if not suggestions:
        suggestions.append({
            "priority": "P2",
            "category": "通用",
            "action": "人工排查所有失败用例",
            "detail": "所有失败用例需要逐一手工复现, 确认根本原因",
            "affected_cases": [a.get("test_case", "") for a in analyses][:5],
        })

    return suggestions


def _generate_summary_text(
    category_distribution: Dict[str, int],
    top_category: Optional[str],
    pass_rate: float,
    ai_count: int,
) -> str:
    """生成分析摘要文本 (for entry file print)"""
    parts = []
    for cat, count in sorted(category_distribution.items(), key=lambda x: x[1], reverse=True):
        cn = CATEGORY_CN.get(cat, cat)
        parts.append(f"{cn} {count} 条")

    text = "，".join(parts) if parts else "所有用例通过，无需分析"

    # 添加AI增强标记
    if ai_count > 0:
        text += f" (其中 {ai_count} 条由AI精准分析)"

    # 添加通过率
    text += f" | 通过率: {pass_rate}%"

    return text


# ══════════════════════════════════════════════════════════════
# Schema 变更检测: detect_schema_changes()
#   (backend/failure_analyzer.py L340-376 同款)
#
#   为什么需要 Schema 变更检测？
#     断言失败最常见的原因就是接口 Schema 变了——
#     字段改名、新增必填字段、返回类型从 int 变成 string。
#     DeepDiff 可以做结构化比较，精确定位"什么字段变了"。
#
#   DeepDiff 不可用时降级到基于 keys 的简单 diff。
# ══════════════════════════════════════════════════════════════

def detect_schema_changes(
    old_schema: Dict[str, Any],
    new_schema: Dict[str, Any],
) -> Dict[str, Any]:
    """
    检测接口 Schema 变更——使用 DeepDiff 或 keys diff。

    Args:
        old_schema: 旧版本 schema (来自接口文档)
        new_schema: 新版本 schema (来自实际响应)

    Returns:
        {has_changes, changes: [{field, old_value, new_value, change_type}],
         summary, suggested_actions}
    """
    changes = []

    # —— 路径1: DeepDiff (主路径) ——
    try:
        from deepdiff import DeepDiff

        diff = DeepDiff(old_schema, new_schema, ignore_order=True)
        if not diff:
            return {"has_changes": False, "changes": [], "summary": "Schema 无变更"}

        # 解析 DeepDiff 结果
        for change_type, items in diff.items():
            if change_type == "values_changed":
                for path, detail in items.items():
                    changes.append({
                        "field": path.replace("root['", "").replace("']", "").replace("']['", "."),
                        "old_value": str(detail.get("old_value", ""))[:100],
                        "new_value": str(detail.get("new_value", ""))[:100],
                        "change_type": "modified",
                    })
            elif change_type == "dictionary_item_added":
                for path in items:
                    path_str = str(path).replace("root['", "").replace("']", "")
                    changes.append({
                        "field": path_str,
                        "old_value": None,
                        "new_value": str(items[path])[:100] if isinstance(items, dict) else str(path),
                        "change_type": "added",
                    })
            elif change_type == "dictionary_item_removed":
                for path in items:
                    path_str = str(path).replace("root['", "").replace("']", "")
                    changes.append({
                        "field": path_str,
                        "old_value": str(items[path])[:100] if isinstance(items, dict) else str(path),
                        "new_value": None,
                        "change_type": "removed",
                    })

        # 建议操作
        actions = []
        if any(c["change_type"] == "removed" for c in changes):
            actions.append("有字段被删除：检查断言是否引用了已删除字段")
        if any(c["change_type"] == "added" for c in changes):
            actions.append("有新增字段：确认是否需要添加对应断言")
        if any(c["change_type"] == "modified" for c in changes):
            actions.append("有字段类型变更：更新数据工厂和断言逻辑")

        return {
            "has_changes": True,
            "changes": changes,
            "total_changes": len(changes),
            "summary": f"检测到 {len(changes)} 处 Schema 变更"
                       f" ({sum(1 for c in changes if c['change_type']=='added')} 新增, "
                       f"{sum(1 for c in changes if c['change_type']=='removed')} 删除, "
                       f"{sum(1 for c in changes if c['change_type']=='modified')} 修改)",
            "suggested_actions": actions,
            "detection_mode": "deepdiff",
        }

    except ImportError:
        pass  # 降级到简单 diff

    # —— 路径2: 简单 keys diff (降级) ——
    old_keys = set(old_schema.get("properties", old_schema).keys()) if isinstance(old_schema, dict) else set()
    new_keys = set(new_schema.get("properties", new_schema).keys()) if isinstance(new_schema, dict) else set()

    added = new_keys - old_keys
    removed = old_keys - new_keys
    common = old_keys & new_keys

    for key in list(added)[:10]:
        changes.append({"field": key, "old_value": None, "new_value": "exists", "change_type": "added"})
    for key in list(removed)[:10]:
        changes.append({"field": key, "old_value": "exists", "new_value": None, "change_type": "removed"})

    # 检查类型/值变更
    if isinstance(old_schema, dict) and isinstance(new_schema, dict):
        old_props = old_schema.get("properties", old_schema)
        new_props = new_schema.get("properties", new_schema)
        for key in common & set(old_props.keys()) & set(new_props.keys()):
            ov = old_props[key]
            nv = new_props[key]
            # 字典值 → 对比 type 子字段；标量值 → 直接对比
            if isinstance(ov, dict) and isinstance(nv, dict):
                ot = ov.get("type", "")
                nt = nv.get("type", "")
                if ot != nt:
                    changes.append({
                        "field": key, "old_value": f"type={ot}",
                        "new_value": f"type={nt}", "change_type": "modified",
                    })
            elif ov != nv:
                changes.append({
                    "field": key,
                    "old_value": str(ov)[:100],
                    "new_value": str(nv)[:100],
                    "change_type": "modified",
                })

    if not changes:
        return {"has_changes": False, "changes": [], "summary": "Schema 无显著变更", "detection_mode": "keys_diff"}

    return {
        "has_changes": True,
        "changes": changes,
        "total_changes": len(changes),
        "summary": f"检测到 {len(changes)} 处 Schema 变更 (keys 模式)",
        "suggested_actions": [
            "请手动验证变更的字段是否影响现有断言",
            "更新数据工厂中的字段映射",
        ],
        "detection_mode": "keys_diff",
    }


# ══════════════════════════════════════════════════════════════
# AISuggestionService — 5 维度建议体系
#   (backend/ai_suggestion_service.py L1-295 同款)
#
#   5 个维度:
#     1. regenerate  — 重新生成（接口定义变了）
#     2. update      — 更新断言（响应 Schema 变了）
#     3. datafix     — 修复数据（测试数据不对）
#     4. config      — 配置调整（环境/超时等）
#     5. investigate — 人工排查（AI 无法确定的复杂问题）
#
#   每个建议包含: priority (P0/P1/P2), effort_estimate (预估工时),
#   confidence (置信度), affected_cases (影响的用例列表)
# ══════════════════════════════════════════════════════════════

class AISuggestionService:
    """
    AI 优化建议服务——5 维度建议体系。

    使用示例:
        svc = AISuggestionService(analyses)
        suggestions = svc.generate_suggestions()
        for s in suggestions:
            print(f"[{s['priority']}] {s['dimension']}: {s['action']}")
    """

    def __init__(self, analyses: List[Dict[str, Any]]):
        self.analyses = analyses
        self._llm_available = Config.is_llm_available()

    def generate_suggestions(self) -> List[Dict[str, Any]]:
        """
        生成 5 维度优化建议。
        先走规则引擎做基础分类，LLM 可用时增强置信度低的建议。
        """
        suggestions = []

        # 按分类聚合
        by_category = defaultdict(list)
        for a in self.analyses:
            cat = a.get("category", "unknown")
            by_category[cat].append(a)

        # 维度1: regenerate — 接口定义变了
        bug_analyses = by_category.get("code_bug", [])
        assertion_failures = [a for a in bug_analyses if a.get("subcategory") == "assertion_error"]
        if assertion_failures:
            suggestions.append({
                "dimension": "regenerate",
                "priority": "P0" if len(assertion_failures) > len(self.analyses) * 0.3 else "P1",
                "action": "重新生成用例——接口 Schema 可能已变更",
                "reason": f"{len(assertion_failures)} 条断言失败，可能因接口响应结构变化",
                "effort_estimate": f"{len(assertion_failures) * 2} 分钟 (自动重新生成)",
                "confidence": 0.82,
                "affected_cases": [a.get("test_case", "") for a in assertion_failures[:5]],
                "implementation": "运行 demo_06 重新生成用例，使用最新接口文档",
            })

        # 维度2: update — 更新断言
        if assertion_failures:
            suggestions.append({
                "dimension": "update",
                "priority": "P1",
                "action": "更新断言——对比实际响应调整 expected 值",
                "reason": f"断言期望值与实际响应不匹配",
                "effort_estimate": f"{len(assertion_failures) * 1} 分钟",
                "confidence": 0.78,
                "affected_cases": [a.get("test_case", "") for a in assertion_failures[:5]],
                "implementation": "用 Postman 手动调接口获取实际响应 → 更新断言",
            })

        # 维度3: datafix — 修复数据
        data_analyses = by_category.get("data", [])
        if data_analyses:
            suggestions.append({
                "dimension": "datafix",
                "priority": "P0" if len(data_analyses) > len(self.analyses) * 0.2 else "P1",
                "action": "修复测试数据——Token/资源ID 传递链路检查",
                "reason": f"{len(data_analyses)} 条数据相关问题（Token 认证/资源不存在）",
                "effort_estimate": f"{len(data_analyses) * 3} 分钟",
                "confidence": 0.85,
                "affected_cases": [a.get("test_case", "") for a in data_analyses[:5]],
                "implementation": (
                    "① 确认 login 用例在所有业务用例之前执行; "
                    "② 检查 Authorization header 的 Bearer token 注入; "
                    "③ 验证 CREATE 产出的资源 ID 是否正确传递到 READ/UPDATE/DELETE"
                ),
            })

        # 维度4: config — 配置调整
        env_analyses = by_category.get("environment", [])
        if env_analyses:
            suggestions.append({
                "dimension": "config",
                "priority": "P2",
                "action": "调整环境配置——网络/超时/并发数调优",
                "reason": f"{len(env_analyses)} 条环境问题（网络超时/服务不可达）",
                "effort_estimate": "30 分钟",
                "confidence": 0.72,
                "affected_cases": [a.get("test_case", "") for a in env_analyses[:3]],
                "implementation": (
                    "① 降低并发数 (concurrency=3 → 1); "
                    "② 增加请求超时时间; "
                    "③ 检查 MockResponder 是否覆盖所有场景"
                ),
            })

        # 维度5: investigate — 人工排查
        unknown_analyses = by_category.get("unknown", [])
        if unknown_analyses:
            suggestions.append({
                "dimension": "investigate",
                "priority": "P2",
                "action": "人工排查未知分类的失败用例",
                "reason": f"{len(unknown_analyses)} 条失败原因未明确分类",
                "effort_estimate": f"{len(unknown_analyses) * 5} 分钟 (人工排查)",
                "confidence": 0.50,
                "affected_cases": [a.get("test_case", "") for a in unknown_analyses[:5]],
                "implementation": "逐条手工复现 → 确认根本原因 → 归类到上述4维之一",
            })

        # LLM 增强：对置信度 < 0.75 的建议做 AI 二次确认
        if self._llm_available:
            for s in suggestions:
                if s["confidence"] < 0.75:
                    s["ai_reviewed"] = False  # 标记待 AI 审核（实际环境调用 LLM）

        return suggestions


# ══════════════════════════════════════════════════════════════
# LLM 断言生成: generate_assertions_via_llm()
#   (backend/ai_suggestion_service.py L180-250 同款)
#
#   为什么用 LLM 生成断言？人工写断言容易遗漏边界情况，
#   LLM 根据接口 Schema + 业务描述可自动生成 3 层断言：
#     L1 状态码 + L2 JSON 结构字段存在性 + L3 字段类型/范围校验
# ══════════════════════════════════════════════════════════════

def generate_assertions_via_llm(
    api_info: Dict[str, Any],
    response_sample: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    使用 LLM 为接口自动生成断言。

    Args:
        api_info: 接口信息 {name, method, path, response_schema, description}
        response_sample: 响应示例（可选，用于 LLM 参考实际结构）

    Returns: [{type, field, expected, description, level}]
    """
    if not Config.is_llm_available():
        return _generate_basic_assertions(api_info)

    schema = api_info.get("response_schema", {}).get("schema", {})
    props = schema.get("properties", {})

    schema_brief = json.dumps({
        "properties": {k: v.get("type", "unknown") for k, v in list(props.items())[:10]},
        "required": schema.get("required", []),
    }, ensure_ascii=False)

    sample_brief = ""
    if response_sample:
        sample_brief = json.dumps(response_sample, ensure_ascii=False)[:300]

    prompt = f"""为以下 API 接口生成 3 层测试断言。

接口: {api_info.get('method', 'GET')} {api_info.get('path', '')}
名称: {api_info.get('name', '')}
描述: {api_info.get('description', '')}

响应 Schema: {schema_brief}
响应示例: {sample_brief or '无'}

请生成 3 层断言:
  1. 状态码断言: status_code == expected_status
  2. 结构断言: 关键字段存在性
  3. 类型断言: 字段类型和范围校验

返回纯 JSON 数组:
[
  {{"type": "status_code", "field": null, "expected": 200, "description": "...", "level": 1}},
  {{"type": "exists", "field": "data", "expected": "not_null", "description": "...", "level": 2}},
  {{"type": "type_check", "field": "data.device_id", "expected": "str", "description": "...", "level": 3}}
]
"""
    try:
        result = llm_client.extract_json(prompt, temperature=0.3, max_tokens=1000)
        if isinstance(result, list):
            return result
    except Exception as e:
        print(f"  [WARN] LLM 断言生成失败: {e}")

    return _generate_basic_assertions(api_info)


def _generate_basic_assertions(api_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """基础断言生成 (LLM 不可用时的兜底)"""
    method = api_info.get("method", "GET").upper()
    expected_status = {"POST": 201, "GET": 200, "PUT": 200, "PATCH": 200, "DELETE": 204}.get(method, 200)

    assertions = [
        {"type": "status_code", "field": None, "expected": expected_status,
         "description": f"期望 HTTP {expected_status}", "level": 1},
    ]

    schema = api_info.get("response_schema", {}).get("schema", {})
    props = schema.get("properties", {})

    # L2: 字段存在性断言
    for field in list(props.keys())[:5]:
        assertions.append({
            "type": "exists", "field": field, "expected": "not_null",
            "description": f"响应应包含 {field} 字段", "level": 2,
        })

    # L3: 字段类型断言
    for field, info in list(props.items())[:5]:
        ft = info.get("type", "string") if isinstance(info, dict) else "string"
        assertions.append({
            "type": "type_check", "field": field, "expected": ft,
            "description": f"{field} 应为 {ft} 类型", "level": 3,
        })

    return assertions
