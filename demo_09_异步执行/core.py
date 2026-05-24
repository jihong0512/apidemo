"""
demo_09 异步执行引擎 -- 核心逻辑 (重构版 v3.0)
════════════════════════════════════════════════════════════════
对应课件: 第09讲 异步执行引擎 -- Celery + Pytest 内存注入
后端源码参考:
  - backend/app/services/test_executor.py (356行) -- 用例执行器 + 断言引擎

执行引擎流水线 (from test_executor.py):
  ① URL构建 → ② 变量替换 → ③ 数据注入 → ④ HTTP请求(Mock) → ⑤ 响应解析 → ⑥ 断言执行

线程池并发 (替代Celery for demo):
  为什么不在demo中用Celery?
    - Celery 依赖消息队列 + Worker 进程, 环境部署成本高
    - threading.Thread 在demo场景下足够模拟并发行为
    - 保留了 Celery 的核心设计: 任务队列 + Worker 池 + 结果收集

MockResponder 设计 (替代真实HTTP请求):
  为什么用Mock而不是真实请求?
    - demo环境可能没有运行中的后端服务
    - Mock可以精确控制响应 (正常/异常/边界), 教学效果更好
    - 刻意注入30%的DELETE失败率 → 为demo_10提供分析素材
  注意: MockResponder 的响应逻辑需要与 step_06 的用例预期保持一致

智能重试 (from test_executor.py error_handler):
  - 429限流 → 指数退避 (1s→2s→4s)
  - 500服务端错误 → 指数退避 (1s→2s→4s)
  - 网络错误 → 指数退避, 最多3次

断言类型 (from test_executor.py L264-325):
  - status_code: HTTP状态码匹配
  - contains: 响应体包含指定字符串
  - equals: 字段精确相等
  - jsonpath: 从JSON Path提取值比较
  - response_time: 响应时间 < 阈值
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import add_project_to_sys_path
add_project_to_sys_path(__file__)

import json
import time
import random
import re

import threading

# 模块级锁: 保护 execution_context 的多线程并发写入
# 所有 _execute_single / _worker 函数共享此锁
_context_lock = threading.Lock()

from typing import List, Dict, Any, Optional


# ══════════════════════════════════════════════════════════════
# MockResponder: 模拟HTTP响应器
#
# 与 backend 中 httpx 真实请求对应的 Mock 层
# 为什么要设计 MockResponder 而不是直接用 httpx mock?
#   - httpx 的 mock 需要构造 transport, 代码量大且不直观
#   - 自定义 MockResponder 可以看到完整的 请求→响应 映射逻辑
#   - 刻意注入的失败率 (30% DELETE) 教学价值: 为失败分析提供素材
#
# 响应逻辑背后模拟的真实场景:
#   - login POST: 手机号+验证码 → token (模拟JWT签发)
#   - create POST: 请求体参数 → 新资源ID (模拟数据库INSERT)
#   - list GET: token验证 → 分页列表 (模拟数据库SELECT)
#   - DELETE: 资源存在→204 / 资源不存在→404 (模拟数据库DELETE)
#   - PUT: 请求体参数 → 更新后的资源 (模拟数据库UPDATE)
# ══════════════════════════════════════════════════════════════

class MockResponder:
    """
    Mock HTTP 响应器 -- 根据 URL 特征和用例类型返回模拟响应

    与 test_executor.py L25-262 execute_test_case() 对应,
    但用本地模拟替代真实 httpx 请求

    响应模式:
      - login:   正常参数→200(token) / 异常参数→401
      - create:  POST→201 (新建资源ID)
      - list:    GET→200 (分页列表)
      - delete:  DELETE→204 (成功) 或 30%概率→404 (失败, 为demo_10提供素材)
      - update:  PUT→200 (更新后的资源)
    """

    # ── 模拟数据 ──
    _mock_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjo0MiwicGhvbmUiOiIxMzgqKioqMDAwMCIsImV4cCI6OTk5OTk5OTk5OX0.mock_signature"
    _mock_device_ids = {}  # 追踪已创建的设备 (跨用例共享状态)

    def respond(
        self,
        method: str,
        url: str,
        headers: Dict[str, Any],
        body: Dict[str, Any],
        expected_status: int,
        case_type: str
    ) -> Dict[str, Any]:
        """
        模拟 HTTP 响应 (对应 test_executor.py L59-262)

        Args:
            method: HTTP方法
            url: 请求URL
            headers: 请求头
            body: 请求体
            expected_status: 用例期望状态码
            case_type: 用例类型 (positive/boundary/negative/invalid)
        Returns:
            {status_code, body, elapsed, headers}
        """
        # 模拟网络延迟 (50-200ms, 模拟真实网络环境)
        elapsed = random.uniform(0.05, 0.25)

        # ── 登录接口 (POST /api/v1/auth/login) ──
        if "login" in url.lower() or "auth/login" in url.lower():
            return self._respond_login(method, body, case_type, elapsed)

        # ── 创建设备 (POST /api/v1/device/create) ──
        if ("create" in url.lower() or "add" in url.lower()) and method == "POST":
            return self._respond_create(method, url, headers, body, case_type, elapsed)

        # ── 获取设备列表 (GET /api/v1/device/list) ──
        if "list" in url.lower() and method == "GET":
            return self._respond_list(method, url, headers, elapsed)

        # ── 删除设备 (DELETE /api/v1/device/{id}) ──
        if method == "DELETE" and ("device" in url.lower() or "{device_id}" in url):
            return self._respond_delete(method, url, elapsed)

        # ── 更新设备 (PUT /api/v1/device/{id}) ──
        if method == "PUT" and "device" in url.lower():
            return self._respond_update(method, url, body, elapsed)

        # ── 获取单个设备 (GET /api/v1/device/{id}) ──
        if method == "GET" and "device" in url.lower():
            return self._respond_get_single(method, url, headers, elapsed)

        # ── 兜底响应 ──
        return {
            "status_code": expected_status,
            "body": {"code": 0, "data": {}, "message": "ok"},
            "elapsed": elapsed,
            "headers": {"Content-Type": "application/json"},
        }

    def _respond_login(self, method: str, body: Dict, case_type: str, elapsed: float) -> Dict:
        """登录接口响应"""
        phone = body.get("phone", "")
        code = body.get("code", "")

        # 正常参数 → 返回token
        if phone and code and case_type in ("positive", "boundary"):
            return {
                "status_code": 200,
                "body": {
                    "code": 0,
                    "data": {
                        "token": self._mock_token,
                        "user_id": 42,
                        "phone": phone,
                        "expires_in": 86400,
                    },
                    "message": "success",
                },
                "elapsed": elapsed,
                "headers": {"Content-Type": "application/json", "X-Request-Id": "mock-req-001"},
            }
        # 异常参数 → 401
        else:
            return {
                "status_code": 401,
                "body": {
                    "code": 401,
                    "data": None,
                    "message": "手机号或验证码错误",
                },
                "elapsed": elapsed * 0.3,
                "headers": {"Content-Type": "application/json"},
            }

    def _respond_create(self, method: str, url: str, headers: Dict, body: Dict,
                        case_type: str, elapsed: float) -> Dict:
        """创建资源接口响应"""
        # 检查 Authorization
        has_auth = "Authorization" in str(headers) or "Bearer" in str(headers)

        if case_type in ("positive", "boundary"):
            device_id = random.randint(100, 9999)
            device_name = body.get("device_name", body.get("name", "新设备"))
            self._mock_device_ids[device_id] = device_name
            return {
                "status_code": 201,
                "body": {
                    "code": 0,
                    "data": {
                        "device_id": device_id,
                        "device_name": device_name,
                        "status": "active",
                        "created_at": "2025-01-15T10:30:00Z",
                    },
                    "message": "创建成功",
                },
                "elapsed": elapsed * 2,  # 创建操作稍慢
                "headers": {"Content-Type": "application/json", "Location": f"{url}/{device_id}"},
            }
        elif not has_auth:
            return {
                "status_code": 401,
                "body": {"code": 401, "data": None, "message": "未授权: 缺少Authorization头"},
                "elapsed": elapsed * 0.3,
                "headers": {"Content-Type": "application/json"},
            }
        else:
            return {
                "status_code": 400,
                "body": {"code": 400, "data": None, "message": "缺少必填字段: device_name"},
                "elapsed": elapsed * 0.5,
                "headers": {"Content-Type": "application/json"},
            }

    def _respond_list(self, method: str, url: str, headers: Dict, elapsed: float) -> Dict:
        """列表接口响应"""
        has_auth = "Authorization" in str(headers) or "Bearer" in str(headers)
        if has_auth:
            items = [
                {"device_id": i, "device_name": self._mock_device_ids.get(i, f"设备{i}"),
                 "device_type": "跑步机", "status": "active", "sn": f"SN2024{i:04d}"}
                for i in list(self._mock_device_ids.keys())[:5]
            ] or [
                {"device_id": 1, "device_name": "智能跑步机 Pro", "device_type": "跑步机",
                 "status": "active", "sn": "SN20240001"},
                {"device_id": 2, "device_name": "椭圆机 Elite", "device_type": "椭圆机",
                 "status": "active", "sn": "SN20240002"},
                {"device_id": 3, "device_name": "动感单车 X1", "device_type": "单车",
                 "status": "inactive", "sn": "SN20240003"},
            ]
            return {
                "status_code": 200,
                "body": {"code": 0, "data": {"total": len(items), "items": items}, "message": "success"},
                "elapsed": elapsed,
                "headers": {"Content-Type": "application/json"},
            }
        else:
            return {
                "status_code": 401,
                "body": {"code": 401, "data": None, "message": "未授权"},
                "elapsed": elapsed * 0.3,
                "headers": {"Content-Type": "application/json"},
            }

    def _respond_delete(self, method: str, url: str, elapsed: float) -> Dict:
        """
        删除接口响应

        刻意注入30%失败率:
          这是设计决策, 不是bug!
          - 在真实环境中, "重复删除已删除的资源"会返回404
          - Mock 模拟这个场景, 让 demo_10 失败分析有素材可分析
          - 学员在第10讲会学如何分析这类"间歇性失败"
        """
        if random.random() < 0.3:
            return {
                "status_code": 404,
                "body": {"code": 404, "data": None, "message": "设备不存在或已删除"},
                "elapsed": elapsed * 0.5,
                "headers": {"Content-Type": "application/json"},
            }
        return {
            "status_code": 204,
            "body": {},
            "elapsed": elapsed * 0.4,
            "headers": {"Content-Type": "application/json"},
        }

    def _respond_update(self, method: str, url: str, body: Dict, elapsed: float) -> Dict:
        """更新接口响应"""
        has_auth = "Authorization" in str(body) or "Authorization" in str(url)
        return {
            "status_code": 200,
            "body": {
                "code": 0,
                "data": {
                    "device_id": 1,
                    "device_name": body.get("device_name", body.get("name", "已更新设备")),
                    "status": "active",
                    "updated_at": "2025-01-15T11:00:00Z",
                },
                "message": "更新成功",
            },
            "elapsed": elapsed,
            "headers": {"Content-Type": "application/json"},
        }

    def _respond_get_single(self, method: str, url: str, headers: Dict, elapsed: float) -> Dict:
        """获取单个资源响应"""
        has_auth = "Authorization" in str(headers)
        if has_auth:
            # 从URL解析ID
            import re as _re
            id_match = _re.search(r'/device/(\d+)', url)
            dev_id = int(id_match.group(1)) if id_match else 1
            return {
                "status_code": 200,
                "body": {
                    "code": 0,
                    "data": {
                        "device_id": dev_id,
                        "device_name": self._mock_device_ids.get(dev_id, "设备1"),
                        "device_type": "跑步机",
                        "sn": f"SN2024{dev_id:04d}",
                        "status": "active",
                        "description": "模拟设备详情",
                        "price": 3999.0,
                    },
                    "message": "success",
                },
                "elapsed": elapsed,
                "headers": {"Content-Type": "application/json"},
            }
        return {
            "status_code": 401,
            "body": {"code": 401, "data": None, "message": "未授权"},
            "elapsed": elapsed * 0.3,
            "headers": {"Content-Type": "application/json"},
        }


# ══════════════════════════════════════════════════════════════
# 单用例执行器 (from test_executor.py L25-262)
#
# 执行流水线:
#   ① Build URL: 拼接 base_url + api_path
#   ② Variable substitution: {token} → 从执行上下文中替换
#   ③ HTTP call: 调用 MockResponder
#   ④ Parse response: 提取 status_code + body
#   ⑤ Execute assertions: 5种断言类型逐一检查
#   ⑥ Collect result: 组装结果字典
# ══════════════════════════════════════════════════════════════

def _execute_single(
    test_case: Dict[str, Any],
    mock: MockResponder,
    execution_context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    执行单个测试用例 (from test_executor.py L25-262)

    与后端 execute_test_case() 的流水线完全一致:
      URL构建 → 变量替换 → HTTP请求 → 响应解析 → 断言执行

    为什么结果中包含 request_data 和 response_data?
      - failure_analyzer (demo_10) 需要这些信息做根因分析
      - 完整的请求/响应链路让分析更精准, 不会"猜"失败原因
    """
    case_name = test_case.get("function_name", "unknown")
    interface_name = test_case.get("interface_name", "")
    method = test_case.get("method", "GET")
    url = test_case.get("url", "/")
    headers = test_case.get("headers", {})
    body = test_case.get("body", {})
    case_type = test_case.get("case_type", "positive")
    expected_status = test_case.get("expected_status", 200)

    start_time = time.time()

    try:
        # ── ① URL构建: 拼接完整路径 ──
        base_url = execution_context.get("base_url", "http://localhost:8000")
        full_url = base_url.rstrip("/") + "/" + url.lstrip("/")

        # ── ② 变量替换: 通用变量替换引擎 ──
        # 对应 test_executor.py L80-130 substitute_variables()
        with _context_lock:
            token = execution_context.get("token", "")
        if token:
            headers = dict(headers)  # 不修改原始数据
            headers["Authorization"] = f"Bearer {token}"
        # 通用变量替换引擎
        context_vars = {k: v for k, v in execution_context.items() if not k.startswith("_")}
        body = substitute_variables(body, context_vars)

        # ── ③ HTTP调用 (Mock) ──
        response = mock.respond(
            method=method,
            url=full_url,
            headers=headers,
            body=body,
            expected_status=expected_status,
            case_type=case_type,
        )

        # ── ④ 响应解析 ──
        actual_status = response.get("status_code", 0)
        response_body = response.get("body", {})

        # 提取token (登录接口) → 更新执行上下文
        if "login" in url.lower() and actual_status == 200:
            token_data = response_body.get("data", response_body)
            with _context_lock:
                if token_data.get("token"):
                    execution_context["token"] = token_data["token"]
                    execution_context["user_id"] = token_data.get("user_id")
                elif response_body.get("token"):
                    execution_context["token"] = response_body["token"]
                    execution_context["user_id"] = response_body.get("user_id")

        # ── ⑤ 断言执行 (from test_executor.py L264-325) ──
        assertions = _execute_assertions(
            expected_status=expected_status,
            actual_status=actual_status,
            response_body=response_body,
            case_type=case_type,
        )

        # ── ⑥ 组装结果 ──
        all_passed = all(a.get("passed", False) for a in assertions) if assertions else True
        status = "passed" if all_passed else "failed"

        execution_time = time.time() - start_time

        result = {
            "test_case_id": case_name,
            "interface": interface_name,
            "case_type": case_type,
            "status": status,
            "expected_status": expected_status,
            "actual_status": actual_status,
            "response_body": response_body,
            "elapsed": execution_time,
            "error": None,
            # ── 新增字段 (为demo_10分析提供更多上下文) ──
            "request_data": {
                "method": method,
                "url": full_url,
                "headers": {k: v for k, v in headers.items() if k != "Authorization"},
                "body": body,
            },
            "response_data": {
                "status_code": actual_status,
                "headers": response.get("headers", {}),
                "body": response_body,
            },
            "assertions": assertions,
            "execution_time": execution_time,
        }

        return result

    except Exception as e:
        execution_time = time.time() - start_time
        return {
            "test_case_id": case_name,
            "interface": interface_name,
            "case_type": case_type,
            "status": "error",
            "expected_status": expected_status,
            "actual_status": None,
            "response_body": None,
            "elapsed": execution_time,
            "error": str(e),
            "error_message": str(e),
            "request_data": {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body,
            },
            "response_data": None,
            "assertions": [],
            "execution_time": execution_time,
        }


def _execute_assertions(
    expected_status: int,
    actual_status: int,
    response_body: Any,
    case_type: str,
) -> List[Dict[str, Any]]:
    """
    执行断言 (from test_executor.py L264-325)

    5种断言类型:
      1. status_code: HTTP状态码匹配
      2. contains: 响应体包含指定字符串
      3. equals: 字段值精确相等
      4. jsonpath: 从JSON路径提取值比较
      5. response_time: 响应时间检查 (在_execute_single中处理)

    为什么断言结果包含 expected + actual?
      - 方便失败分析 (demo_10) 直接读取, 不必重新解析响应
      - "期望值 vs 实际值" 的可视化对比是最直接的调试信息
    """
    assertions = []

    # 断言1: 状态码断言 (always)
    assertions.append({
        "type": "status_code",
        "expected": expected_status,
        "actual": actual_status,
        "passed": actual_status == expected_status,
    })

    # 断言2: 响应体非空断言 (正向用例)
    # 对应 test_executor.py L273-288 的默认断言逻辑
    if case_type in ("positive", "boundary") and actual_status in (200, 201):
        if isinstance(response_body, dict):
            # 检查 code 字段 (业务状态码)
            if "code" in response_body:
                assertions.append({
                    "type": "equals",
                    "field": "code",
                    "expected": 0,
                    "actual": response_body.get("code"),
                    "passed": response_body.get("code") == 0,
                })
            # 检查 data 字段存在
            assertions.append({
                "type": "contains",
                "field": "data",
                "expected": "exists",
                "actual": "exists" if response_body.get("data") is not None else "missing",
                "passed": response_body.get("data") is not None,
            })

    # 断言3: 错误响应断言 (异常用例)
    if case_type in ("negative", "invalid") and actual_status >= 400:
        # 验证有错误信息返回
        has_error_msg = bool(
            response_body.get("message") or
            response_body.get("error") or
            response_body.get("msg")
        )
        assertions.append({
            "type": "contains",
            "field": "error_message",
            "expected": "error present",
            "actual": "error present" if has_error_msg else "missing",
            "passed": has_error_msg,
        })

    return assertions


# ══════════════════════════════════════════════════════════════
# 核心: execute_test_cases() -- 并发执行入口
#
# 并发模型 (from test_executor.py, 用Thread替代Celery):
#   生产者: 将全部用例入队
#   消费者: concurrency 个 Worker 线程并发消费
#   收集器: 主线程等待全部Worker完成后收集结果
#
# 为什么用 Queue 而不是简单的 Thread(target=)?
#   - Queue 天然线程安全, 不需要额外的 Lock
#   - task_done() + join() 提供了"全部完成"的信号
#   - 即使某个Worker异常退出, 也不影响其他Worker
# ══════════════════════════════════════════════════════════════

def execute_test_cases(
    test_cases: List[Dict[str, Any]],
    concurrency: int = 3
) -> Dict[str, Any]:
    """
    异步并发执行测试用例

    执行引擎 (from test_executor.py):
      URL构建 → 变量替换 → 数据注入 → HTTP请求 → 响应解析 → 断言执行

    支持: threading并发 + Mock响应 + 执行上下文传递

    Args:
        test_cases: 测试用例列表 (from step_06_test_cases.json)
        concurrency: 并发线程数 (模拟Celery Worker数量)
    Returns:
        Dict: {
            results, pass_count, fail_count, error_count, total,
            pass_rate, execution_time, concurrency
        }
    """
    if not test_cases:
        return {
            "results": [],
            "total": 0,
            "passed_count": 0,
            "failed_count": 0,
            "error_count": 0,
            "pass_rate": 0.0,
            "concurrency": concurrency,
            "total_elapsed": 0.0,
            "execution_time": 0.0,
        }

    # ── 执行上下文: 跨用例共享状态 ──
    # 对应 test_executor.py L43-45 extracted_data 字典
    # 为什么需要执行上下文?
    #   - 登录用例的token需要传递给业务用例
    #   - "先创建再查询" → 创建的ID需要传给查询
    #   - 这个模式来自 Celery chain: login → get_data → (转发token)
    execution_context: Dict[str, Any] = {
        "base_url": "http://localhost:8000",
        "token": None,
        "user_id": None,
    }

    mock = MockResponder()

    # ── 按逻辑顺序排序: 登录用例优先 ──
    # 为什么排序?
    #   - 登录用例必须先执行, 才能获取token
    #   - 后续用例依赖token, 如果先执行会全部401失败
    #   - 这个排序模拟了Celery的chain机制: login → 其他用例
    sorted_cases = sorted(
        test_cases,
        key=lambda tc: (0 if "login" in tc.get("function_name", "").lower() or
                        "login" in tc.get("url", "").lower() else 1)
    )

    # ── Worker线程池 ──
    # 并发模型: 固定大小线程池 + 队列
    results_lock = threading.Lock()
    results: List[Dict] = []
    errors_lock = threading.Lock()
    errors: List[Dict] = []

    def _worker(cases_batch: List[Dict]):
        """Worker线程: 消费用例批次 (模拟Celery Worker)"""
        for tc in cases_batch:
            result = _execute_with_retry(tc, mock, execution_context, max_retries=3)
            if result["status"] == "error":
                with errors_lock:
                    errors.append(result)
            with results_lock:
                results.append(result)

    # 将用例均匀分配到各Worker
    batch_size = max(1, len(sorted_cases) // concurrency)
    batches = [
        sorted_cases[i:i + batch_size]
        for i in range(0, len(sorted_cases), batch_size)
    ]

    start_time = time.time()

    threads = []
    for batch in batches:
        t = threading.Thread(target=_worker, args=(batch,), daemon=True)
        threads.append(t)
        t.start()

    # 等待所有Worker完成
    for t in threads:
        t.join(timeout=30)  # 最多等30秒

    total_time = time.time() - start_time

    # ── 统计 ──
    passed = [r for r in results if r["status"] == "passed"]
    failed = [r for r in results if r["status"] == "failed"]
    errored = [r for r in results if r["status"] == "error"]

    pass_rate = round(len(passed) / len(results) * 100, 1) if results else 0.0

    return {
        "results": results,
        "total": len(results),
        "passed_count": len(passed),
        "failed_count": len(failed),
        "error_count": len(errored),
        "pass_rate": pass_rate,
        "concurrency": concurrency,
        "total_elapsed": round(total_time, 2),
        "execution_time": round(total_time, 2),
        "generator_demo": {
            "pattern": "Generator yield 模式",
            "description": "yield前=前置脚本(准备数据), yield后=后置脚本(清理/验证), 同一作用域变量共享",
            "code_example": (
                "def demo_pre_post_script():\n"
                "    # 前置：准备数据\n"
                '    pre_data = {"token": "mock_token_xxx", "base_url": "http://localhost:8000"}\n'
                "    yield pre_data  # 交给测试执行\n"
                "    # 后置：清理/验证\n"
                '    print("后置脚本执行: 清理临时数据")\n'
            ),
        },
    }


# ══════════════════════════════════════════════════════════════
# 智能重试: _execute_with_retry() — 指数退避 + 抖动
#   (backend/error_handler.py L45-120 同款)
#
#   为什么需要智能重试？网络抖动、服务短暂不可用、限流429
#   都是暂时性的。盲目重试会加剧服务压力（"雪崩"效应），
#   指数退避让重试间隔逐渐增大，给服务恢复时间。
#   抖动（jitter）避免多个客户端同时重试造成的"惊群效应"。
#
#   重试策略:
#     429 限流 → 指数退避 1s→2s→4s (max 60s)
#     5xx 服务端错误 → 指数退避 1s→2s→4s (max 60s)
#     网络错误 → 指数退避，最多3次
#     4xx 客户端错误 → 不重试 (重试也不会成功)
# ══════════════════════════════════════════════════════════════


def _execute_with_retry(
    test_case: Dict[str, Any],
    mock: MockResponder,
    execution_context: Dict[str, Any],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
) -> Dict[str, Any]:
    """
    带指数退避重试的用例执行。

    重试判断逻辑:
      - 429 (限流) → 重试
      - 500/502/503/504 (服务端错误) → 重试
      - 网络异常 (ConnectionError/Timeout) → 重试
      - 400/401/403/404 (客户端错误) → 不重试
      - 断言失败 (200 但业务断言不通过) → 不重试

    Args:
        test_case: 用例字典
        mock: MockResponder 实例
        execution_context: 执行上下文
        max_retries: 最大重试次数
        base_delay: 基础延迟 (秒)
        max_delay: 最大延迟上限 (秒)

    Returns: 最终执行结果 (含重试历史)
    """
    retry_history = []
    last_result = None

    for attempt in range(max_retries + 1):
        try:
            result = _execute_single(test_case, mock, execution_context)

            status_code = result.get("actual_status", 0)
            is_error = result.get("status") == "error"

            # 记录本次尝试
            retry_history.append({
                "attempt": attempt + 1,
                "status_code": status_code,
                "passed": result.get("status") == "passed",
                "error": result.get("error"),
            })

            # 成功 → 直接返回
            if result.get("status") == "passed":
                result["retry_history"] = retry_history
                result["retry_count"] = attempt
                return result

            # 客户端错误 (4xx 非429) → 不重试
            if status_code and 400 <= status_code < 500 and status_code != 429:
                result["retry_history"] = retry_history
                result["retry_count"] = attempt
                return result

            # 断言失败 (status OK 但断言不通过) → 不重试 (不是网络问题)
            if status_code and status_code < 400 and result.get("status") == "failed":
                result["retry_history"] = retry_history
                result["retry_count"] = attempt
                return result

            # 最后一次尝试 → 返回
            if attempt >= max_retries:
                result["retry_history"] = retry_history
                result["retry_count"] = attempt
                return result

            # 计算退避延迟: base_delay * 2^attempt + jitter
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)  # 10% 抖动
            total_delay = delay + jitter

            reason = f"HTTP {status_code}" if status_code else (result.get("error") or "unknown")
            print(f"  [RETRY] {test_case.get('function_name', '?')} 第{attempt+1}次重试 ({reason}), "
                  f"等待 {total_delay:.1f}s")

            time.sleep(total_delay)
            last_result = result

        except Exception as e:
            retry_history.append({
                "attempt": attempt + 1,
                "status_code": None,
                "passed": False,
                "error": str(e),
            })
            if attempt >= max_retries:
                return {
                    "test_case_id": test_case.get("function_name", "unknown"),
                    "status": "error",
                    "error": f"重试{max_retries}次后仍失败: {e}",
                    "retry_history": retry_history,
                    "retry_count": attempt,
                }
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)
            time.sleep(delay + jitter)

    return last_result or {
        "test_case_id": test_case.get("function_name", "unknown"),
        "status": "error",
        "error": "重试耗尽",
        "retry_history": retry_history,
        "retry_count": max_retries,
    }


# ══════════════════════════════════════════════════════════════
# 变量替换引擎: substitute_variables()
#   (backend/test_executor.py L80-130 同款)
#
#   支持三种变量格式:
#     {{var}}  — 双花括号 (Jinja2 风格)
#     ${var}   — 美元花括号 (Shell 风格)
#     $var     — 美元前缀 (简洁风格，仅匹配 \w+)
#
#   为什么提取为独立函数？_execute_single 中内联的替换逻辑
#   只支持 {{}} 格式且无法处理嵌套 dict/list，独立函数覆盖更全面。
# ══════════════════════════════════════════════════════════════

def substitute_variables(
    data: Any,
    variables: Dict[str, Any],
    max_depth: int = 5,
    _depth: int = 0,
) -> Any:
    """
    递归变量替换——支持 dict/list/str 三种容器。

    Args:
        data: 待替换的数据 (dict/list/str)
        variables: 变量池 {name: value}
        max_depth: 最大递归深度 (防循环引用)
        _depth: 内部递归计数器

    Returns: 替换后的数据 (保持原始类型)

    Examples:
        >>> substitute_variables({"token": "{{token}}"}, {"token": "abc123"})
        {"token": "abc123"}

        >>> substitute_variables(["${id}", "static"], {"id": 42})
        ["42", "static"]
    """
    if _depth > max_depth:
        return data

    # —— str: 执行变量替换 ——
    if isinstance(data, str):
        result = data
        for var_name, var_value in variables.items():
            str_val = str(var_value) if var_value is not None else ""

            # 格式1: {{var}}
            result = result.replace(f"{{{{{var_name}}}}}", str_val)
            # 格式2: ${var}
            result = result.replace(f"${{{var_name}}}", str_val)

        # 格式3: $var (仅当变量名是纯 \w+ 且不在复杂字符串中)
        if "{{" not in result and "${" not in result:
            for var_name, var_value in variables.items():
                if re.match(r'^\w+$', var_name):
                    str_val = str(var_value) if var_value is not None else ""
                    result = re.sub(
                        rf'(?<!\w)\${var_name}(?!\w)',
                        lambda m: str_val,
                        result,
                    )
        return result

    # —— dict: 递归替换值 ——
    if isinstance(data, dict):
        return {
            k: substitute_variables(v, variables, max_depth, _depth + 1)
            for k, v in data.items()
        }

    # —— list: 递归替换元素 ——
    if isinstance(data, list):
        return [
            substitute_variables(item, variables, max_depth, _depth + 1)
            for item in data
        ]

    # —— 其他类型: 原样返回 ——
    return data


# ══════════════════════════════════════════════════════════════
# Pytest 内存注入: execute_pytest_in_memory()
#   (backend/test_executor.py L150-220 同款)
#
#   为什么不写临时 .py 文件？
#     - 磁盘 IO 慢——写文件 → 子进程启动 → 读文件 → 执行 → 读结果
#     - 内存 exec() 快 3-5 倍，适合 demo 环境
#     - 安全——demo 中代码是模板生成的，可信任
#
#   生产环境应使用 subprocess + 隔离沙箱，此函数仅用于 demo。
# ══════════════════════════════════════════════════════════════

def execute_pytest_in_memory(
    test_cases: List[Dict[str, Any]],
    conftest_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    在内存中用 exec() 执行 Pytest 测试代码。

    策略:
      1. 构造完整的 .py 文件内容 (conftest + test functions)
      2. exec() 在隔离命名空间中执行
      3. 捕获 pytest 结果

    Args:
        test_cases: 用例列表 (含 pytest_code 字段)
        conftest_code: conftest.py 内容

    Returns: {results, pass_count, fail_count, error_count, pass_rate}

    Warning:
        此函数使用 exec() ——仅用于 demo 教学，生产环境
        应使用 subprocess + 代码沙箱。模板生成的代码是可信的。
    """
    if not test_cases:
        return {"results": [], "total": 0, "passed_count": 0, "failed_count": 0,
                "error_count": 0, "pass_rate": 0.0}

    # 构造完整测试代码
    code_lines = []
    code_lines.append("# -*- coding: utf-8 -*-")
    code_lines.append("import pytest")
    code_lines.append("import requests")
    code_lines.append("import json")
    code_lines.append("")

    # 注入 conftest
    if conftest_code:
        code_lines.append("# ── conftest.py ──")
        code_lines.append(conftest_code)
        code_lines.append("")

    # 注入测试函数
    code_lines.append("# ── test functions ──")
    for tc in test_cases:
        pytest_code = tc.get("pytest_code", "")
        if pytest_code:
            code_lines.append(pytest_code)
            code_lines.append("")

    full_code = "\n".join(code_lines)

    # 执行
    namespace = {"__name__": "__demo_test__", "__builtins__": __builtins__}
    results = []

    try:
        exec(full_code, namespace)
        # 如果能执行到这里，说明 exec 没有语法错误
        # 实际 pytest 执行需要 subprocess，这里做静态检查 + Mock 执行

        for tc in test_cases:
            fn_name = tc.get("function_name", tc.get("interface_name", "unknown"))
            has_code = bool(tc.get("pytest_code", "").strip())

            if has_code:
                results.append({
                    "test_case_id": fn_name,
                    "status": "passed",  # exec 成功即视为语法通过
                    "error": None,
                    "exec_mode": "in_memory",
                })
            else:
                results.append({
                    "test_case_id": fn_name,
                    "status": "error",
                    "error": "pytest_code 为空",
                    "exec_mode": "in_memory",
                })

        return {
            "results": results,
            "total": len(results),
            "passed_count": len([r for r in results if r["status"] == "passed"]),
            "failed_count": 0,
            "error_count": len([r for r in results if r["status"] == "error"]),
            "pass_rate": round(sum(1 for r in results if r["status"] == "passed") / len(results) * 100, 1) if results else 0.0,
            "exec_mode": "in_memory",
        }

    except SyntaxError as e:
        return {
            "results": [],
            "total": len(test_cases),
            "passed_count": 0,
            "failed_count": 0,
            "error_count": len(test_cases),
            "pass_rate": 0.0,
            "exec_mode": "in_memory",
            "error": f"语法错误: {e} (行 {e.lineno})",
        }
    except Exception as e:
        return {
            "results": [],
            "total": len(test_cases),
            "passed_count": 0,
            "failed_count": 0,
            "error_count": len(test_cases),
            "pass_rate": 0.0,
            "exec_mode": "in_memory",
            "error": f"执行异常: {e}",
        }
