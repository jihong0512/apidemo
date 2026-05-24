"""
代码demo公共工具模块 v2.0
══════════════════════════════════════════════════════════════
所有 demo 通过此模块统一：
  1. JSON 数据契约读写（shared_data/ 目录）
  2. Neo4j 图数据库连接（带重试/锁/降级）
  3. LLM 客户端（DeepSeek API，与 backend/llm_service.py 同款）
  4. 配置管理（从 .env 或环境变量加载）
  5. 日志/进度打印

设计原则：
  - 每个 demo 独立可跑（服务不可用时自动降级到本地模式）
  - 串联时通过 shared_data/step_NN_xxx.json 传递数据
  - 真实代码模式与 backend/ 源码一致
══════════════════════════════════════════════════════════════
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional
from threading import Lock

# ══════════════════════════════════════════════════════════════
# Windows 兼容：stdout 使用 UTF-8
# ══════════════════════════════════════════════════════════════
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ══════════════════════════════════════════════════════════════
# 路径常量
# ══════════════════════════════════════════════════════════════
SHARED_DIR = Path(__file__).parent / "shared_data"
DEMO_DIR = Path(__file__).parent

# ══════════════════════════════════════════════════════════════
# 配置管理 —— 从 .env 文件或环境变量加载
# ══════════════════════════════════════════════════════════════

def _load_dotenv():
    """加载 .env 文件（项目根目录或代码demo目录）"""
    env_paths = [
        DEMO_DIR / ".env",
        DEMO_DIR.parent / ".env",
        DEMO_DIR.parent.parent.parent / "backend" / ".env",  # apitest/backend/.env
        Path(".env"),
    ]
    for env_path in env_paths:
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, value = line.partition("=")
                        key, value = key.strip(), value.strip().strip('"').strip("'")
                        if key and key not in os.environ:
                            os.environ[key] = value
            break

_load_dotenv()


class Config:
    """配置管理（与 backend/config.py 的 settings 对应）"""

    # ── Neo4j 图数据库 ──
    NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password")

    # ── DeepSeek LLM API ──
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    # ── 通义千问 (Embedding & Reranker) ──
    QWEN_API_KEY = os.environ.get("QWEN_API_KEY", os.environ.get("DASHSCOPE_API_KEY", ""))
    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-v3")
    RERANKER_MODEL = os.environ.get("RERANKER_MODEL", "gte-rerank")

    # ── MySQL (SQLAlchemy) ──
    MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
    MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
    MYSQL_USER = os.environ.get("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
    MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "api_test")

    # ── ChromaDB 持久化 ──
    CHROMA_PERSIST_DIR = os.environ.get("CHROMA_PERSIST_DIR", str(DEMO_DIR / "chroma_data"))

    # ── 降级开关 ──
    DEMO_MODE = os.environ.get("DEMO_MODE", "auto")  # "auto" | "force_local" | "force_real"

    @classmethod
    def is_neo4j_available(cls) -> bool:
        """检查 Neo4j 是否配置并可用"""
        if cls.DEMO_MODE == "force_local":
            return False
        if cls.DEMO_MODE == "force_real":
            return True
        return bool(os.environ.get("NEO4J_URI"))

    @classmethod
    def is_llm_available(cls) -> bool:
        """检查 LLM API Key 是否配置"""
        if cls.DEMO_MODE == "force_local":
            return False
        if cls.DEMO_MODE == "force_real":
            return True
        return bool(cls.DEEPSEEK_API_KEY)

    @classmethod
    def is_mysql_available(cls) -> bool:
        """检查 MySQL 是否配置并可用"""
        if cls.DEMO_MODE == "force_local":
            return False
        if cls.DEMO_MODE == "force_real":
            return True
        return bool(os.environ.get("MYSQL_HOST"))

    @classmethod
    def is_qwen_available(cls) -> bool:
        """检查通义千问 API Key 是否配置"""
        if cls.DEMO_MODE == "force_local":
            return False
        if cls.DEMO_MODE == "force_real":
            return True
        return bool(cls.QWEN_API_KEY)

    @classmethod
    def status_report(cls) -> str:
        """打印服务可用性状态"""
        lines = [
            "══════════════════════════════════════",
            "  服务连接状态",
            "──────────────────────────────────────",
            f"  MySQL:    {'✓ 已配置' if cls.is_mysql_available() else '✗ 未配置 (离线模式)'}",
            f"  Neo4j:    {'✓ 已配置' if cls.is_neo4j_available() else '✗ 未配置 (降级到 networkx)'}",
            f"  LLM:      {'✓ 已配置' if cls.is_llm_available() else '✗ 未配置 (降级到规则引擎)'}",
            f"  Embedding:{'✓ 已配置' if cls.is_qwen_available() else '✗ 未配置 (降级到 hash 向量)'}",
            f"  Demo模式: {cls.DEMO_MODE}",
            "══════════════════════════════════════",
        ]
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# Neo4j 图数据库连接管理
#   与 backend/db_service.py 同款模式：
#   - Lock 保护重连逻辑
#   - 连接失败自动降级
#   - _neo4j_driver 延迟初始化
# ══════════════════════════════════════════════════════════════

class Neo4jManager:
    """
    Neo4j 图数据库连接管理器
    与 backend/db_service.py 的 DatabaseService 同款设计：
      - 延迟初始化（首次调用时才连接）
      - Lock 保护重入
      - 认证限流等待
      - 连接失败自动降级
    """

    def __init__(self):
        self._driver = None
        self._lock = Lock()
        self._last_connect_time = 0
        self._connect_delay = 5  # 连接失败后延迟5秒重试
        self._available = None  # None=未检测, True=可用, False=不可用

    def _init_driver(self):
        """初始化 Neo4j driver（与 db_service.py L28-50 同款逻辑）"""
        try:
            from neo4j import GraphDatabase
            from neo4j.exceptions import AuthError, ServiceUnavailable

            self._driver = GraphDatabase.driver(
                Config.NEO4J_URI,
                auth=(Config.NEO4J_USER, Config.NEO4J_PASSWORD),
                connection_timeout=10,
                max_connection_lifetime=3600,
            )
            # 测试连接
            with self._driver.session() as session:
                session.run("RETURN 1")
            self._available = True
            return True
        except ImportError:
            print("  [WARN] neo4j 驱动未安装，降级到 networkx 本地模式")
            print("  → 安装: pip install neo4j")
            self._available = False
            return False
        except Exception as e:
            error_str = str(e)
            if "AuthenticationRateLimit" in error_str:
                print(f"  [WARN] Neo4j 认证被锁定，{self._connect_delay}秒后重试")
            elif "Unable to retrieve routing information" in error_str:
                print(f"  [WARN] Neo4j 无法连接 ({Config.NEO4J_URI})，降级到 networkx")
            else:
                print(f"  [WARN] Neo4j 连接失败: {e}")
            self._available = False
            return False

    def get_session(self):
        """获取 Neo4j 会话（与 db_service.py L52-81 同款）"""
        if not Config.is_neo4j_available():
            return None

        with self._lock:
            current_time = time.time()

            if self._available is None:
                if not self._init_driver():
                    return None

            if not self._available:
                # 尝试重连
                if current_time - self._last_connect_time < self._connect_delay:
                    return None
                self._last_connect_time = current_time
                if not self._init_driver():
                    return None

            if self._driver is None:
                return None

            try:
                return self._driver.session()
            except Exception as e:
                print(f"  [WARN] Neo4j 会话创建失败: {e}")
                self._available = False
                return None

    def is_available(self) -> bool:
        """检查 Neo4j 是否可用（不创建连接，只检查缓存状态）"""
        if not Config.is_neo4j_available():
            return False
        if self._available is None:
            return self._init_driver()
        return self._available

    def close(self):
        """关闭 Neo4j 连接"""
        if self._driver:
            try:
                self._driver.close()
            except Exception:
                pass
            self._driver = None
            self._available = None


# 全局单例
neo4j_manager = Neo4jManager()


# ══════════════════════════════════════════════════════════════
# LLM 客户端（DeepSeek）
#   与 backend/llm_service.py 同款：
#   - chat() 基础对话
#   - extract_structured_data() 结构化提取
#   - generate_test_case() 用例生成
#   - analyze_error() 失败分析
# ══════════════════════════════════════════════════════════════

class LLMClient:
    """
    LLM 客户端封装（与 backend/llm_service.py 同款）
    支持 DeepSeek API，失败时返回空结果不抛异常
    """

    def __init__(self):
        self._client = None

    def _ensure_client(self):
        """延迟初始化 OpenAI 客户端"""
        if self._client is None and Config.is_llm_available():
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=Config.DEEPSEEK_API_KEY,
                    base_url=Config.DEEPSEEK_BASE_URL,
                )
            except ImportError:
                print("  [WARN] openai 包未安装，LLM 功能不可用")
                print("  → 安装: pip install openai")
                return False
            except Exception as e:
                print(f"  [WARN] LLM 客户端初始化失败: {e}")
                return False
        return self._client is not None

    def chat(
        self,
        prompt: str,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> Optional[str]:
        """
        调用 LLM 进行对话（与 llm_service.py L21-41 同款）
        失败时返回 None（不抛异常，由调用方处理降级）
        """
        if not self._ensure_client():
            return None

        if messages is None:
            messages = [{"role": "user", "content": prompt}]

        try:
            response = self._client.chat.completions.create(
                model=Config.DEEPSEEK_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"  [LLM] 调用失败: {e}")
            return None

    def extract_json(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4000,
    ) -> Optional[Dict[str, Any]]:
        """
        调用 LLM 并提取 JSON 响应（与 llm_service.py L43-62 同款）
        自动从 LLM 文本响应中提取 JSON

        核心提示词模式（来自 backend/prompt_engineer.py）：
          - 角色定义 → 任务描述 → 输入数据 → 输出格式 → JSON Schema
        """
        result = self.chat(prompt, temperature=temperature, max_tokens=max_tokens)
        if result is None:
            return None

        # ── 从 LLM 响应中提取 JSON ──
        #   LLM 常在 JSON 前后加解释文字，需要正则提取
        #   与 backend/document_parser.py L293 和 failure_analyzer.py L122 同款
        import re

        # 方法1：匹配 ```json ... ``` 代码块
        json_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", result)
        if json_block:
            try:
                return json.loads(json_block.group(1))
            except json.JSONDecodeError:
                pass

        # 方法2：匹配最外层花括号（支持嵌套）
        json_match = re.search(r"\{[\s\S]*\}", result)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # 方法3：find + rfind 截取
        start = result.find("{")
        end = result.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(result[start : end + 1])
            except json.JSONDecodeError:
                pass

        print(f"  [LLM] JSON 提取失败，原始响应: {result[:200]}...")
        return None

    def get_langchain_llm(self):
        """
        返回 LangChain 兼容的 ChatOpenAI 实例，供 GraphCypherQAChain 等使用。

        demo_03 的 query_graph_natural_language() 通过此方法获取 LLM
        来驱动 LangChain 的 Cypher 生成链。LLM 不可用时返回 None，
        调用方自动降级到关键词匹配路径。
        """
        if not self._ensure_client():
            return None
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=Config.DEEPSEEK_MODEL,
                openai_api_key=Config.DEEPSEEK_API_KEY,
                openai_api_base=Config.DEEPSEEK_BASE_URL,
            )
        except ImportError:
            print("  [WARN] langchain_openai 未安装，GraphCypherQAChain 不可用")
            print("  → 安装: pip install langchain-openai")
            return None


# 全局单例
llm_client = LLMClient()


# ══════════════════════════════════════════════════════════════
# JSON 数据契约读写（原有功能保持）
# ══════════════════════════════════════════════════════════════

def ensure_shared_dir():
    """确保 shared_data 目录存在"""
    SHARED_DIR.mkdir(parents=True, exist_ok=True)


def read_json(filename: str) -> dict:
    """
    读取 shared_data 中的 JSON 文件
    文件不存在时给出明确的错误提示（告诉学员应该先跑哪个 demo）
    """
    filepath = SHARED_DIR / filename
    if not filepath.exists():
        step_hint = _step_filename_to_hint(filename)
        raise FileNotFoundError(
            f"\n  [ERR] 找不到输入文件: {filename}\n"
            f"  ---> 请先运行 {step_hint}\n"
        )
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def read_json_safe(filename: str, default: dict = None) -> dict:
    """读取 JSON 文件，不存在时返回默认值（不退出）"""
    filepath = SHARED_DIR / filename
    if not filepath.exists():
        return default if default is not None else {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(filename: str, data: dict):
    """
    写入 JSON 到 shared_data，自动注入元信息
    """
    ensure_shared_dir()
    filepath = SHARED_DIR / filename
    if "_meta" not in data:
        data["_meta"] = {}
    data["_meta"]["generated_at"] = datetime.now().isoformat()
    data["_meta"]["source_file"] = filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  [OK] 已写入: {filename}  ({_count_items(data)} 条记录)")


def print_header(step_num: int, title: str):
    """打印步骤标题"""
    bar = "=" * 60
    print(f"\n{bar}")
    print(f"  Step {step_num:02d} | {title}")
    print(f"{bar}")


def print_step(step_num: int, name: str, input_file: str, output_file: str):
    """打印当前步骤信息"""
    print(f"\n  > 当前: demo_{step_num:02d} - {name}")
    src = f"shared_data/{input_file}" if input_file else "(内存数据)"
    print(f"  > 输入: {src}")
    print(f"  > 输出: shared_data/{output_file}")


def print_summary(step_num: int, results: Dict[str, Any]):
    """打印步骤摘要"""
    for key, value in results.items():
        if key.startswith("_"):
            continue
        if isinstance(value, list):
            print(f"  > {key}: {len(value)} 条")
        elif isinstance(value, dict):
            print(f"  > {key}: {len(value)} 个键")
        else:
            print(f"  > {key}: {value}")


def print_success(step_num: int):
    """打印成功标记"""
    print(f"\n  [OK] Step {step_num:02d} 完成\n")


def print_service_status():
    """打印当前服务连接状态"""
    print(Config.status_report())


def _make_result_summary(data: dict) -> str:
    """从步骤结果中生成简短摘要"""
    if not data:
        return "(empty)"
    # 优先使用明确的总计字段
    for key in ("total_failures", "total_cases", "total_interfaces"):
        if key in data:
            return f"共 {data[key]} 项"
    # 否则用键值数量
    n = _count_items(data)
    return f"{n} 条记录" if n else "已处理"


def _count_items(data: dict) -> int:
    """统计数据中的主要条目数（跳过元信息）"""
    for key in data:
        if key.startswith("_"):
            continue
        if isinstance(data[key], list):
            return len(data[key])
        if isinstance(data[key], dict):
            return len(data[key])
    return 1


def _step_filename_to_hint(filename: str) -> str:
    """从文件名推断应该先跑哪个 demo"""
    mapping = {
        "sample_swagger.json": "准备示例 Swagger 文档（已内置在 shared_data/ 中）",
        "step_02_interfaces.json": "demo_02_文档解析/parse_document.py",
        "step_03_knowledge_graph.json": "demo_03_知识图谱/build_graph.py",
        "step_04_dependencies.json": "demo_04_依赖分析/analyze_deps.py",
        "step_05_test_data.json": "demo_05_数据工厂/generate_data.py",
        "step_06_test_cases.json": "demo_06_用例生成/generate_cases.py",
        "step_07_rag_index.json": "demo_07_RAG知识库/build_index.py",
        "step_08_agent_cases.json": "demo_08_Agent编排/run_agent.py",
        "step_09_execution_results.json": "demo_09_异步执行/execute_cases.py",
        "step_10_analysis.json": "demo_10_失败分析/analyze_results.py",
    }
    return mapping.get(filename, f"上一个 demo（生成 {filename} 的步骤）")


# ══════════════════════════════════════════════════════════════
# 项目路径初始化 —— 消除各 demo 文件中的 sys.path 样板
# ══════════════════════════════════════════════════════════════

def add_project_to_sys_path(caller_file: str):
    """
    将代码demo 根目录加入 sys.path，使所有 demo 能 import demo_common。

    替代之前 18 个文件中的样板代码:
      sys.path.insert(0, str(Path(__file__).parent.parent))

    用法（在各 demo 的 entry/core .py 文件顶部）:
      from demo_common import add_project_to_sys_path
      add_project_to_sys_path(__file__)
    """
    import sys as _sys
    from pathlib import Path as _Path
    project_root = str(_Path(caller_file).resolve().parent.parent)
    if project_root not in _sys.path:
        _sys.path.insert(0, project_root)


# ══════════════════════════════════════════════════════════════
# 环境检查 —— 运行前验证 Python 版本、必需包、目录结构
# ══════════════════════════════════════════════════════════════

def check_demo_prerequisites(step_num: int) -> bool:
    """
    检查运行 demo 的前置条件，不满足时打印提示（不退出）。

    检查项:
      1. Python 版本 >= 3.9
      2. shared_data/ 目录存在
      3. 必需的输入文件存在（如果能推断）
      4. 基础依赖包可导入 (networkx)

    Returns: True 表示所有检查通过
    """
    import sys as _sys

    all_ok = True

    # ① Python 版本
    py_ver = _sys.version_info
    if py_ver < (3, 9):
        print(f"  [WARN] Python 版本 {py_ver.major}.{py_ver.minor} < 3.9，建议升级")
        all_ok = False

    # ② shared_data 目录
    if not SHARED_DIR.exists():
        print(f"  [WARN] shared_data/ 目录不存在，将在首次写入时自动创建")
        print(f"  → 目录: {SHARED_DIR}")

    # ③ 基础依赖
    for pkg_name, pkg_hint in [
        ("networkx", "pip install networkx"),
    ]:
        try:
            __import__(pkg_name)
        except ImportError:
            print(f"  [WARN] {pkg_name} 未安装 (demo_03 知识图谱降级)")
            print(f"  → 安装: {pkg_hint}")

    # ④ 可选的 .env 文件
    env_paths = [DEMO_DIR / ".env", DEMO_DIR.parent / ".env"]
    env_found = any(p.exists() for p in env_paths)
    if not env_found:
        print("  [INFO] 未检测到 .env 文件，所有外部服务将自动降级到本地模式")
        print("  → 参考: requirements.txt 中的 .env 配置说明")

    return all_ok


# ══════════════════════════════════════════════════════════════
# PipelineStep / PipelineRunner —— 消除 run_pipeline.py 与 entry 的重复代码
# ══════════════════════════════════════════════════════════════

class PipelineStep:
    """
    流水线单步封装 —— read → call → write 模式。

    为什么需要这个类？
      - 每个 entry 脚本都重复: read_json(input) → core.xxx(data) → write_json(output)
      - run_pipeline.py 又重复一遍同样的调用逻辑
      - PipelineStep 把这三步封装成一个对象，entry 和 pipeline 共用

    用法:
      step = PipelineStep(
          step_num=2, name="文档解析",
          input_file="sample_swagger.json",
          output_file="step_02_interfaces.json",
          runner=parse_swagger_document,
      )
      result = step.run()  # 返回 core 函数的返回值
    """

    def __init__(self, step_num: int, name: str, input_file: str,
                 output_file: str, runner: callable,
                 require_input: bool = True):
        self.step_num = step_num
        self.name = name
        self.input_file = input_file
        self.output_file = output_file
        self.runner = runner
        self.require_input = require_input
        self.elapsed = 0.0

    def run(self) -> dict:
        """执行本步骤: 读取输入 → 调用核心函数 → 写入输出"""
        print_header(self.step_num, self.name)
        print_step(self.step_num, self.name, self.input_file, self.output_file)

        start = time.time()

        # 读取输入 (input_file 为 None 时跳过文件读取)
        if self.input_file is None:
            input_data = None
        elif self.require_input:
            input_data = read_json(self.input_file)
        else:
            input_data = read_json_safe(self.input_file)

        # 调用核心函数
        result = self.runner(input_data)

        # 写入输出
        if isinstance(result, dict):
            write_json(self.output_file, result)

        self.elapsed = time.time() - start
        print_success(self.step_num)
        return result


class PipelineRunner:
    """
    多步流水线执行器 —— 按顺序执行 PipelineStep 列表。

    与 run_pipeline.py 中的手动调用不同，PipelineRunner 提供:
      - 统一的进度展示
      - 每步耗时统计
      - 失败时自动停止（可配置）
      - 最终汇总表

    用法:
      runner = PipelineRunner(stop_on_error=True)
      runner.add_step(PipelineStep(...))
      runner.add_step(PipelineStep(...))
      runner.run_all()
    """

    def __init__(self, stop_on_error: bool = True):
        self.steps: List[PipelineStep] = []
        self.stop_on_error = stop_on_error
        self.results: Dict[int, dict] = {}

    def add_step(self, step: PipelineStep):
        """向流水线添加一个步骤"""
        self.steps.append(step)

    def run_all(self, from_step: int = 1, to_step: Optional[int] = None) -> bool:
        """
        按顺序执行所有步骤。

        Args:
            from_step: 从第几步开始（默认 1）
            to_step: 到第几步结束（默认 None = 全部）
        Returns:
            True: 全部成功
            False: 有步骤失败
        """
        total_start = time.time()

        # 过滤出要执行的步骤
        active_steps = [s for s in self.steps
                        if s.step_num >= from_step
                        and (to_step is None or s.step_num <= to_step)]

        if not active_steps:
            print("  [WARN] 没有匹配的步骤需要执行")
            return True

        print(f"\n{'=' * 60}")
        print(f"  流水线执行: 共 {len(active_steps)} 步 (Step {from_step} → {to_step or len(self.steps)})")
        print(f"{'=' * 60}")

        all_ok = True
        for step in active_steps:
            try:
                result = step.run()
                # 包装为统一的结果字典，供 _print_pipeline_summary 使用
                summary = _make_result_summary(result) if isinstance(result, dict) else str(result)[:40]
                self.results[step.step_num] = {
                    "status": "success",
                    "elapsed": step.elapsed,
                    "summary": summary,
                }
            except FileNotFoundError as e:
                self.results[step.step_num] = {
                    "status": "failed",
                    "elapsed": step.elapsed,
                    "error": f"输入文件缺失: {e}",
                }
                print(f"\n  [ERR] Step {step.step_num:02d} 失败: 输入文件缺失")
                print(f"  {e}")
                if self.stop_on_error:
                    print("  → 流水线已停止（可设置 stop_on_error=False 跳过）")
                    all_ok = False
                    break
                all_ok = False
            except Exception as e:
                self.results[step.step_num] = {
                    "status": "failed",
                    "elapsed": step.elapsed,
                    "error": str(e),
                }
                print(f"\n  [ERR] Step {step.step_num:02d} 失败: {e}")
                if self.stop_on_error:
                    all_ok = False
                    break
                all_ok = False

        # ── 耗时汇总表 ──
        total_elapsed = time.time() - total_start
        self._print_timeline(active_steps, total_elapsed)

        return all_ok

    def _print_timeline(self, steps: List[PipelineStep], total: float):
        """打印步骤耗时汇总表"""
        print(f"\n{'─' * 50}")
        print(f"  {'步骤耗时汇总':^46}")
        print(f"{'─' * 50}")
        print(f"  {'步骤':<8} {'耗时':>8}  {'占比':>6}")
        print(f"  {'─' * 40}")
        for step in steps:
            pct = (step.elapsed / total * 100) if total > 0 else 0
            print(f"  Step {step.step_num:02d}  {step.elapsed:>7.2f}s  {pct:>5.1f}%")
        print(f"  {'─' * 40}")
        print(f"  {'合计':<8} {total:>7.2f}s")
        print(f"{'─' * 50}\n")

        if all(s.elapsed > 0 for s in steps):
            slowest = max(steps, key=lambda s: s.elapsed)
            print(f"  ⏱ 最慢步骤: Step {slowest.step_num:02d} ({slowest.name}, {slowest.elapsed:.2f}s)")


# ══════════════════════════════════════════════════════════════
# MySQL 数据库连接管理
#   所有 demo 统一通过 mysql_manager 获取 SQLAlchemy 会话，
#   无需各自处理 SessionLocal 创建、连接检测、离线降级。
# ══════════════════════════════════════════════════════════════

class MySQLManager:
    """
    MySQL 连接管理器——提供 SQLAlchemy 会话，自动降级到离线模式。

    用法:
        session = mysql_manager.get_session()
        if session is None:
            print("MySQL 不可用，使用离线模式")
            # 处理离线逻辑
        else:
            # 正常的 SQLAlchemy 操作
            result = session.query(SomeModel).all()
            session.close()
    """

    def __init__(self):
        self._engine = None
        self._SessionLocal = None
        self._available = None  # None=未检测, True=可用, False=不可用

    def _init_engine(self) -> bool:
        """初始化 SQLAlchemy engine + sessionmaker"""
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker

            db_url = (
                f"mysql+pymysql://{Config.MYSQL_USER}:{Config.MYSQL_PASSWORD}"
                f"@{Config.MYSQL_HOST}:{Config.MYSQL_PORT}/{Config.MYSQL_DATABASE}"
                f"?charset=utf8mb4"
            )
            self._engine = create_engine(
                db_url,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                pool_recycle=3600,
            )
            self._SessionLocal = sessionmaker(bind=self._engine)

            # 测试连接
            with self._engine.connect() as conn:
                from sqlalchemy import text
                conn.execute(text("SELECT 1"))
            self._available = True
            return True
        except ImportError:
            print("  [WARN] sqlalchemy/pymysql 未安装，MySQL 功能不可用")
            print("  → 安装: pip install sqlalchemy pymysql")
            self._available = False
            return False
        except Exception as e:
            print(f"  [WARN] MySQL 连接失败 ({Config.MYSQL_HOST}:{Config.MYSQL_PORT}): {e}")
            self._available = False
            return False

    def get_session(self):
        """
        获取 SQLAlchemy 会话。
        返回 None 表示 MySQL 不可用——调用方应切换到离线模式。
        """
        if not Config.is_mysql_available():
            return None

        if self._available is None:
            if not self._init_engine():
                return None

        if not self._available or self._SessionLocal is None:
            return None

        try:
            return self._SessionLocal()
        except Exception as e:
            print(f"  [WARN] MySQL 会话创建失败: {e}")
            self._available = False
            return None

    def is_available(self) -> bool:
        """检查 MySQL 是否可用（触发首次连接检测）"""
        if not Config.is_mysql_available():
            return False
        if self._available is None:
            return self._init_engine()
        return self._available

    def close(self):
        """关闭 MySQL 连接池"""
        if self._engine:
            try:
                self._engine.dispose()
            except Exception:
                pass
            self._engine = None
            self._SessionLocal = None
            self._available = None


# 全局单例
mysql_manager = MySQLManager()


# ══════════════════════════════════════════════════════════════
# 智能数据生成器（Faker 字段映射 + OpenAPI Schema 展开）
#   与 demo_05 的 generate_test_data() 互补——那个生成批量 L4 场景数据，
#   这个是单接口的 {params, headers, body, path_params} 字典生成器，
#   适用于 04→05→06 管线中的简单数据生成场景。
# ══════════════════════════════════════════════════════════════

class DataGenerator:
    """
    轻量数据生成器：Faker 字段映射 + Schema 展开。

    用法:
        gen = DataGenerator()
        test_data = gen.generate(api_info)
        # test_data == {"params": {...}, "headers": {...}, "body": {...}, "path_params": {...}}
    """

    def __init__(self):
        self._faker = None
        self._faker_ok = False
        try:
            from faker import Faker
            self._faker = Faker("zh_CN")
            self._faker_ok = True
        except ImportError:
            pass

        self.field_map = {
            "phone": self._gen("phone_number"),
            "mobile": self._gen("phone_number"),
            "password": self._gen("password", length=12),
            "email": self._gen("email"),
            "username": self._gen("user_name"),
            "name": self._gen("name"),
            "nickname": self._gen("name"),
            "title": self._gen("sentence", nb_words=6),
            "content": self._gen("text", max_nb_chars=100),
            "description": self._gen("text", max_nb_chars=50),
            "avatar": self._gen("image_url"),
            "url": self._gen("url"),
            "address": self._gen("address"),
        }

    def _gen(self, method_name: str, **kwargs):
        """返回一个生成器函数——优先用 Faker，不可用则内置降级"""
        import random as _random
        import uuid as _uuid

        _fallbacks = {
            "phone_number": lambda: f"138{_random.randint(10000000, 99999999)}",
            "password": lambda: f"Pwd@{_random.randint(10000, 99999)}",
            "email": lambda: f"user{_random.randint(1, 9999)}@example.com",
            "user_name": lambda: f"user_{_random.randint(100, 9999)}",
            "name": lambda: _random.choice(["张伟", "王芳", "李娜", "刘洋", "陈静", "杨帆", "赵敏", "黄磊"]),
            "sentence": lambda: f"自动化测试{_random.choice(['标题', '主题', '内容'])}{_random.randint(1, 999)}",
            "text": lambda: f"这是由测试数据工厂自动生成的文本内容，编号{_random.randint(1000, 9999)}",
            "image_url": lambda: f"https://picsum.photos/{_random.randint(100,400)}/{_random.randint(100,400)}",
            "url": lambda: f"https://www.example{_random.randint(1, 99)}.com/api",
            "address": lambda: f"北京市朝阳区某某路{_random.randint(1, 200)}号",
        }

        if self._faker_ok and self._faker:
            def _call_faker():
                return getattr(self._faker, method_name)(**kwargs)
            return _call_faker

        return _fallbacks.get(method_name, lambda: f"auto_{_random.randint(1000, 9999)}")

    def _gen_value(self, field_name: str, field_schema=None) -> Any:
        """根据字段名和 Schema 生成值（4 级 fallback）"""
        import random as _random
        name_lower = field_name.lower()

        # 1. 精确匹配 field_map
        if name_lower in self.field_map:
            return self.field_map[name_lower]()

        # 2. 部分匹配（如 course_id 匹配到包含 id 的规则）
        for key, gen_fn in self.field_map.items():
            if key in name_lower:
                return gen_fn()

        # 3. 特殊后缀匹配
        if name_lower.endswith("_id") or name_lower.endswith("id"):
            if "course" in name_lower:
                return f"COURSE_{_random.randint(10000, 99999)}"
            return _random.randint(1, 99999)

        # 4. 按 Schema type 生成
        if field_schema and isinstance(field_schema, dict):
            ftype = field_schema.get("type", "string")
            if ftype == "integer":
                return _random.randint(1, 9999)
            elif ftype == "number":
                return round(_random.uniform(1.0, 100.0), 2)
            elif ftype == "boolean":
                return _random.choice([True, False])

        # 5. 默认
        import random as _r2
        return f"test_{_r2.randint(1000, 9999)}"

    def _extract_schema_body(self, body) -> dict:
        """展开 OpenAPI Schema 结构 → 提取 properties 为扁平的字段-值映射"""
        if not isinstance(body, dict):
            return body if body else {}
        if "schema" in body and isinstance(body["schema"], dict):
            schema = body["schema"]
            props = schema.get("properties", {})
            result = {}
            for fname, fschema in props.items():
                if isinstance(fschema, dict):
                    if "example" in fschema:
                        result[fname] = fschema["example"]
                    elif "default" in fschema:
                        result[fname] = fschema["default"]
                    elif "enum" in fschema and fschema["enum"]:
                        result[fname] = fschema["enum"][0]
                    else:
                        result[fname] = self._gen_value(fname, fschema)
            # 确保 required 字段至少有空值
            for rf in schema.get("required", []):
                if rf not in result:
                    result[rf] = ""
            return result
        return body

    def generate(self, api_info: dict) -> Dict[str, Any]:
        """
        为单个接口生成测试数据 → {params, headers, body, path_params}
        与后端 05 讲的合约完全一致。
        """
        # params: 已有值保留，空值填充
        params = dict(api_info.get("params", {}))
        for k, v in params.items():
            if v == "" or v is None:
                params[k] = self._gen_value(k)

        # headers: 保留原值（如 Content-Type），token 使用占位符
        headers = dict(api_info.get("headers", {}))

        # body: Schema 展开后覆盖
        raw_body = api_info.get("body", {})
        body = self._extract_schema_body(raw_body)

        # path_params: 从 URL 中提取 {param} 占位符
        path_params = {}
        url = api_info.get("url", "")
        for m in re.finditer(r"\{(\w+)\}", url):
            path_params[m.group(1)] = self._gen_value(m.group(1))

        return {"params": params, "headers": headers, "body": body, "path_params": path_params}


# 全局单例
data_generator = DataGenerator()
