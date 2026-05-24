# 代码 Demo 审计报告

> 📅 审计日期：2026-05-24
> 🎯 范围：`apitest/接口测试课件/重构版/代码demo/` 全部 24 个 Python 文件
> 📋 审计维度：死代码/断引用 · LLM连接 · Neo4j连接 · 课件一致性 · 逻辑缺口

---

## 一、总体评分

| 维度 | 评分 | 说明 |
|:---|:---|:---|
| **代码结构** | ⭐⭐⭐⭐⭐ | demo_common.py 统一基础设施，PipelineStep/PipelineRunner 消除重复 |
| **LLM 连接** | ⭐⭐⭐⭐ | 检测逻辑正确，失败自动降级。1 处断引用 |
| **Neo4j 连接** | ⭐⭐⭐⭐⭐ | 双路径（Neo4j/networkx）设计完善，降级安全 |
| **课件一致性** | ⭐⭐⭐⭐ | 5 处可接受的简化差异，详见第四节 |
| **死代码** | ⭐⭐⭐ | 2 处死代码，1 处误导性 guard |
| **安全性** | ⭐⭐⭐ | .env 含真实 API Key（严重） |

---

## 二、P0 级问题（必须修复）

### 2.1 `.env` 包含真实 API Key 🔴 严重

**文件**：`代码demo/.env`

```ini
DEEPSEEK_API_KEY=sk-abebebd1bc2b404fa0556d46912a379e
QWEN_API_KEY=sk-5233a3a4b1a24426b6846a432794bbe2
```

**问题**：这两个 Key 是真实可用的。如果代码 demo 被分享给学员或上传到公开仓库，Key 会泄漏。

**建议**：立即替换为占位符 `your_deepseek_api_key` / `your_qwen_api_key`，并在 `.gitignore` 中加入 `.env`。

---

### 2.2 `demo_03/core.py` L517：`get_langchain_llm()` 永不存在 🟡 中等

**代码**：
```python
chain = GraphCypherQAChain.from_llm(
    llm=llm_client.get_langchain_llm() if hasattr(llm_client, 'get_langchain_llm') else None,
    ...
)
```

**问题**：`LLMClient` 类（`demo_common.py` L272-376）没有 `get_langchain_llm()` 方法。`hasattr` 永远返回 `False`，所以 `llm=None` 被传入 `GraphCypherQAChain.from_llm()`，导致 `TypeError`。虽然被 try/except 包裹后降级到关键词匹配路径，但 **LangChain 路径永远不可达**——这是虚假的"双路径"。

**影响**：
- `query_graph_natural_language()` 的自然语言查询功能看似有 LLM 翻译路径，实际每次都走异常降级
- 如果学员配置了 LLM 后尝试调用这个函数，会发现它"不工作"但也不报错，排查困难

**修复方案**（二选一）：

**方案 A**：在 `LLMClient` 中添加 `get_langchain_llm()` 方法：
```python
def get_langchain_llm(self):
    """返回 LangChain 兼容的 LLM 实例"""
    if not self._ensure_client():
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=Config.DEEPSEEK_MODEL,
        openai_api_key=Config.DEEPSEEK_API_KEY,
        openai_api_base=Config.DEEPSEEK_BASE_URL,
    )
```

**方案 B**：删除 LangChain 路径，直接使用关键词匹配（如果教学不需要演示 GraphCypherQAChain）。

**推荐方案 A**——因为课件中提到了"LangChain GraphCypherQAChain 集成"作为自然语言查询的亮点。

---

## 三、P1 级问题（建议修复）

### 3.1 `query_graph_natural_language()` 是死代码 🟡

**文件**：`demo_03_知识图谱/core.py` L480

**问题**：这个函数（约 120 行）在 demo 代码库中**从未被调用**：
- `build_graph.py` 不调用它
- `_verify_all.py` 不调用它
- `run_pipeline.py` 不调用它
- 其他任何 demo 都不引用它

**建议**：
- 要么在 `build_graph.py` 末尾加一段演示调用（如 `query_graph_natural_language("login依赖哪些接口？")`）
- 要么标记为 `# 可选演示: 在 build_graph.py 中取消注释以体验` 并注释掉引用

### 3.2 `demo_09/core.py`L689：`inspect.getsource` 的不当 guard 🟡

**代码**：
```python
"code_example": inspect.getsource(demo_pre_post_script) if 'inspect' in dir() else "见源码",
```

**问题**：
1. `'inspect' in dir()` 检查的是当前作用域中是否有名为 `inspect` 的变量——`inspect` 已在文件顶部导入，所以这个检查永远为 `True`，guard 形同虚设
2. `demo_pre_post_script()` 是定义在 `execute_test_cases()` 内部的嵌套函数，`inspect.getsource()` 需要从文件的 sourcelines 中定位它——虽然技术上可以做到（通过 `__code__.co_firstlineno`），但嵌套函数的源码获取在不同 Python 版本中行为可能不一致

**建议**：
```python
# 直接用字符串常量替代 inspect.getsource，更安全：
"code_example": (
    "def demo_pre_post_script():\n"
    "    pre_data = {...}  # 前置：准备数据\n"
    "    yield pre_data\n"
    "    # 后置：清理/验证\n"
),
```

### 3.3 `demo_07/core.py`L635：`_use_chromadb` 存入可序列化字典 🟢 轻微

**问题**：`_use_chromadb` (bool) 被存入 `build_rag_index()` 返回的字典，然后被 `write_json()` 写入 `step_07_rag_index.json`。虽然 `bool` 是可 JSON 序列化的，但 `_use_chromadb` 是以 `_` 开头的"内部"字段，混入数据契约有点不够干净。

**当前状态**：实际 JSON 文件 `step_07_rag_index.json` L5058 显示 `"_use_chromadb": false`，说明它确实被写入了。

**建议**：要么去掉 `_` 前缀（`"use_chromadb": false`），要么在写入前移除内部字段。

---

## 四、Demo vs 课件不一致处

### 4.1 demo_06 的 `PytestCaseGenerator` 不含 LLM 双模式 🟡

**课件描述**（`03-06讲-数据流全景说明.md` L426-471）：
- `PytestCaseGenerator.__init__` 接受 `use_llm: bool = False`
- LLM 模式下包含 `self.llm_service = LLMService()` 和 `self.sync_llm = LLMServiceSync()`
- `generate_test_case()` 方法根据 `use_llm` 标志路由到 LLM 或模板路径
- LLM 失败自动回退模板

**Demo 实际**（`demo_06/core.py` L517-707）：
- `PytestCaseGenerator.__init__` 接受 7 个参数（api_info, case_data, framework, test_style, include_fixtures, assertion_level, output_format）
- **没有 `use_llm` 参数**
- **没有 `self.llm_service` 或 `self.sync_llm`**
- 纯模板生成（字符串拼接），不调用 LLM

**分析**：这是**合理的简化**。Demo 把 LLM/模板双模式逻辑上移到了 `generate_test_cases()` 顶层函数中（L42-58），`_llm_generate()` 和 `_tmpl_generate()` 是两个独立函数。`PytestCaseGenerator` 类在 demo 中仅作为"增强生成"的可选步骤使用（L87-114），而非核心生成逻辑。

**建议**：在 `demo_06/core.py` 的文档字符串中加一行说明，避免学员对比课件时困惑：
```python
# 注意：本 demo 的 PytestCaseGenerator 是简化版（纯模板），
# 生产版（课件中的 backend/test_case_generator.py）包含 LLM 双模式路由。
```

### 4.2 demo_08 的 Agent 架构比课件简化 🟢

**课件描述**（`08-Agent编排系统-课件.md` L115-200）：
- `InterfaceParserAgent`：调 `HybridRAGService.hybrid_search()` + `LLMService.chat()`，50 行
- `DependencyAnalyzerAgent`：调 `DatabaseService.get_table_relationships()` + LLM，65 行
- `TestCaseGeneratorAgent`：调 GraphRAG + per-interface LLM 循环，85 行
- 三个 Agent 各自实现完整的 `(State) -> State` 签名

**Demo 实际**（`demo_08/core.py`）：
- `_run_parser_agent()`：RAG 检索 → LLM 选择 → 关键词匹配 fallback
- `_run_dependency_agent()`：Neo4j → demo_04 分析 → LLM → 规则 fallback
- `_run_generator_agent()`：per-interface LLM → 模板 fallback
- 使用 `AgentState` 类（不是 TypedDict），`run_agent_workflow()` 用 LangGraph StateGraph 编排

**差异**：
1. Demo 用的是普通函数（`_run_parser_agent`）而非类（`InterfaceParserAgent`）
2. Demo 的 State 是 `AgentState` 类实例，而课件描述的是 `TypedDict`

**分析**：架构等价——三函数模式和三 Agent 类模式在逻辑上等价。使用函数减少了样板代码，对教学更友好。`AgentState` 类 vs `TypedDict` 的差异不影响功能。

**建议**：无需修改。在 `core.py` 头部注释说明"函数式等价于课件中的 Agent 类模式"即可。

### 4.3 demo_10 的 DeepDiff 实现与课件对齐 ✅

**课件描述**（`10-智能断言-课件.md` L913-959）：
- `api_change_detector.py` 使用 `DeepDiff` 检测 schema 变更
- 4 种变更类型：type_changes / values_changed / dictionary_item_added / dictionary_item_removed

**Demo 实际**（`demo_10/core.py` L801 `detect_schema_changes()`）：
- 使用 `DeepDiff` 作为主路径
- 降级到纯 Python keys 比较
- 输出格式与课件一致

**状态**：✅ 一致。

### 4.4 demo_11 流水线与课件描述一致 ✅

**课件描述**（`11-智能断言失败分析-课件.md` + `12-端到端实战-课件.md`）：
- `full_test_flow()` 按流水线顺序执行

**Demo 实际**（`demo_11/run_pipeline.py`）：
- 使用 `PipelineRunner` + `PipelineStep` 按顺序执行
- CLI 支持 `--from-step` / `--to-step` 部分执行
- 10 步流水线完整覆盖

**差异**：demo_11 不包含第 01 讲（概览），从 step_02 开始到 step_10。因为 step_01 是纯展示，不产生数据。

**状态**：✅ 一致。

### 4.5 demo 数据流中的 `{{var}}` 占位符模式 🟢

**课件描述**（`03-06讲-数据流全景说明.md` L623-638）：
- L05 标记占位符 → L06 拼入代码（保留）→ L09 运行时替换

**Demo 实际**：
- `demo_05` 生成的 `test_data` 中 header 使用 `"Bearer {{token}}"` 占位符 ✅
- `demo_06` 生成的 pytest 代码中保留 `{{token}}` ✅
- `demo_09` 的 `substitute_variables()` 函数运行时会替换 `{{token}}` ✅

**状态**：✅ 三讲之间的数据契约完全一致。

---

## 五、LLM 连接分析

### 5.1 连接检测逻辑

| 组件 | 检测方法 | 正确性 |
|:---|:---|:---|
| `Config.is_llm_available()` | 检查 `DEEPSEEK_API_KEY` 环境变量 | ✅ |
| `Config.DEMO_MODE` | 支持 `"auto"` / `"force_local"` / `"force_real"` | ✅ |
| `LLMClient._ensure_client()` | 延迟初始化 OpenAI 客户端 | ✅ |
| `LLMClient.chat()` | 失败返回 None，不抛异常 | ✅ |
| `LLMClient.extract_json()` | 三层 JSON 提取（``` 代码块 → 花括号正则 → find/rfind） | ✅ |

### 5.2 各 Demo 的 LLM 调用状态

| Demo | 调 LLM？ | 降级路径 | 降级行为 |
|:---|:---|:---|:---|
| demo_02 | ✅ 文档解析增强 | 纯规则解析 | 规则路径完全可用 |
| demo_03 | ✅ `query_graph_natural_language` | 关键词匹配 Cypher | ⚠️ LLM 路径因 P0 问题不可达 |
| demo_04 | ✅ 语义分组 | SequenceMatcher + keyword | ✅ |
| demo_05 | ❌ 不调 LLM（纯 Faker） | — | N/A |
| demo_06 | ✅ 用例生成 | 模板引擎 `_tmpl_generate()` | ✅ |
| demo_07 | ✅ Embedding / Reranker | Hash 向量 / 原始顺序 | ✅ |
| demo_08 | ✅ Agent 推理 | 关键词匹配 / 规则分析 | ✅ |
| demo_09 | ❌ 不调 LLM（纯 Mock） | — | N/A |
| demo_10 | ✅ 失败根因分析 | 7 条 if/elif 规则 | ✅ |
| demo_11 | (透传，不直接调 LLM) | — | N/A |

### 5.3 LLM fallback 完整性评价

**优秀**：demo_06、demo_10 的 LLM→规则双路径设计完善，LLM 失败时降级行为明确。

**需要修复**：demo_03 `query_graph_natural_language` 的 LangChain 路径不可达（P0）。

---

## 六、Neo4j 连接分析

### 6.1 连接检测逻辑

| 组件 | 检测方法 | 正确性 |
|:---|:---|:---|
| `Config.is_neo4j_available()` | 检查 `NEO4J_URI` 环境变量 | ✅ |
| `Neo4jManager._init_driver()` | 延迟初始化 + 测试连接 | ✅ |
| `Neo4jManager.get_session()` | Lock 保护 + 重连 + 降级 | ✅ |

### 6.2 各 Demo 的 Neo4j 使用状态

| Demo | 用 Neo4j？ | 降级路径 | 降级行为 |
|:---|:---|:---|:---|
| demo_03 | ✅ 图写入 + 图查询 | `_NetworkxStorage` | API 完全一致 |
| demo_04 | ✅ 依赖边查询 | Heuristic 推断 | 从 headers/URL 推断依赖 |
| demo_05 | ❌ 不直接调 Neo4j | — | 通过 JSON 数据契约间接触发 |
| demo_06 | ❌ 不调 Neo4j | — | N/A |
| demo_07 | ❌ 不调 Neo4j | — | N/A |
| demo_08 | ✅ 依赖分析查询 | `_rule_based_dependency_analysis()` | ✅ |

### 6.3 Neo4j fallback 完整性评价

**优秀**：策略模式设计（`_GraphStorage` 抽象基类 + `_Neo4jStorage` / `_NetworkxStorage`），两个存储后端的 API 完全一致。未安装 neo4j 驱动的学员可以零配置运行所有 demo。

---

## 七、数据流契约验证（03→06 四讲）

以下验证 `shared_data/` 中各步骤 JSON 文件的数据契约是否一致：

| 步骤 | 输出文件 | 下游消费方 | 字段是否匹配 |
|:---|:---|:---|:---|
| step_02 | `interfaces` (name/method/url/headers/body) | demo_03, 04, 05, 06, 08 | ✅ |
| step_03 | `graph` (nodes/edges) | demo_04 | ✅ |
| step_04 | `scenarios` (call_order/dependency_chain) | demo_05, 06 | ✅ |
| step_05 | `test_data` (params/headers/body) | demo_06 | ✅ |
| step_06 | `test_cases` (function_name/pytest_code) | demo_09 | ✅ |
| step_07 | `rag_index` (documents/embeddings) | demo_08 | ✅ |
| step_08 | `test_cases` (type/name/dependencies) | — | ✅ |
| step_09 | `execution_results` (results/passed_count/…) | demo_10 | ✅ |

**结论**：10 步数据契约完整一致，无断裂。

---

## 八、逻辑充分性评估

### 8.1 过度简化（值得注意）

| 位置 | 简化了什么 | 对教学的影响 |
|:---|:---|:---|
| demo_09 MockResponder | 用内存字典模拟 HTTP 响应，而非真实网络请求 | **可接受**——避免了依赖外部服务，且通过注入 30% DELETE 失败率为 demo_10 提供分析素材，教学价值高 |
| demo_09 线程池 vs Celery | 用 `ThreadPoolExecutor` 替代 Celery | **可接受**——Celery 需要消息队列 + Worker 进程，部署成本太高。核心设计（任务队列、并发、重试）在 demo 中完整保留 |
| demo_08 AgentState | 普通类 vs TypedDict | **可接受**——功能等价，普通类更容易理解 |
| demo_06 PytestCaseGenerator | 纯模板 vs LLM 双模式 | **可接受**——LLM 路径在 `_llm_generate()` 中独立实现，逻辑不丢失 |

### 8.2 逻辑缺口（需要补充）

| 位置 | 缺口 | 建议 |
|:---|:---|:---|
| demo_09 `execute_pytest_in_memory()` | 用 `exec()` 做静态语法检查，**不实际执行 HTTP 请求** | 在函数文档中明确说明这是"语法验证模式"而非"真实执行模式"。真实执行需要 `subprocess` + 代码沙箱 |
| demo_07 `_use_chromadb` | ChromaDB collection 对象不可序列化到 JSON，但 `_use_chromadb` 布尔值会存入 | 小问题，见 3.3 |
| demo_03 `query_graph_natural_language` | LangChain 路径不可达（P0） | 见 2.2 |

---

## 九、代码质量亮点

以下设计值得在课件中强调：

1. **`demo_common.py` 统一基础设施**（1024 行）：消除了 18 个文件中的 `sys.path` 样板、重复的 JSON 读写、重复的服务检测。`Config` 类的 `DEMO_MODE` 让学员可以通过环境变量一键切换"全离线"和"全真实"模式。

2. **策略模式降级设计**：Neo4j→networkx、LLM→规则引擎、ChromaDB→内存字典——每条降级路径都保持 API 一致，学员感知不到切换。

3. **PipelineStep/PipelineRunner**：把每个 demo 的 `read→call→write` 模式封装成可组合的步骤，`run_pipeline.py` 只需声明步骤列表即可跑全链路。

4. **`LLMClient.extract_json()` 三层 fallback**：``` 代码块提取 → 花括号正则 → find/rfind 截取，与 `backend/failure_analyzer.py` L122 和 `backend/document_parser.py` L293 同款逻辑，展示了生产级的容错设计。

5. **demo_09 的 30% DELETE 失败注入**：刻意的不稳定行为为 demo_10 提供了真实的分析素材——这是"测试的测试"思维，教学价值极高。

---

## 十、修复优先级

| 优先级 | 问题 | 位置 | 工作量 |
|:---|:---|:---|:---|
| 🔴 P0 | `.env` 泄露真实 API Key | `.env` | 1 分钟（替换为占位符） |
| 🟡 P1 | `get_langchain_llm()` 不存在 | `demo_03/core.py` L517 | 15 分钟（添加方法到 `LLMClient`） |
| 🟡 P1 | `query_graph_natural_language` 死代码 | `demo_03/core.py` L480 | 10 分钟（添加入口调用） |
| 🟡 P1 | `inspect.getsource` guard 不当 | `demo_09/core.py` L689 | 5 分钟（改为字符串常量） |
| 🟢 P2 | 课件 vs demo 架构差异注释 | `demo_06/core.py`, `demo_08/core.py` | 10 分钟（添加注释） |
| 🟢 P2 | `_use_chromadb` 命名 | `demo_07/core.py` L635 | 5 分钟（去下划线前缀） |

**总修复工作量**：约 1 小时。
