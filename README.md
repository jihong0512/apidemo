# 代码Demo — 12讲AI测试平台实战代码

这是"AI接口自动化测试平台"12讲课程配套的可运行代码示例。每个 demo 对应一讲，**独立可运行**，同时输出 JSON 文件作为下一讲的输入，形成 10 步流水线。

## 一键安装 (Docker + Python)

### Windows
```powershell
.\setup.ps1
```

### macOS / Linux
```bash
chmod +x setup.sh && ./setup.sh
```

脚本自动完成: ✅ Docker检查 → ✅ 启动MySQL/Neo4j → ✅ 健康检查等待 → ✅ Neo4j种子数据 → ✅ Python依赖安装 → ✅ MySQL验证

### 手动安装
```bash
# 1. 启动基础设施
docker compose up -d

# 2. 等待健康检查 (约60秒)
docker ps --format "table {{.Names}}\t{{.Status}}"

# 3. Neo4j 种子数据
docker exec -i api_test_neo4j cypher-shell -u neo4j -p 123456789 < init-neo4j/init.cypher

# 4. Python 依赖
pip install -r requirements.txt
```

## 设计原则

| 原则 | 说明 |
|:---|:---|
| **双模运行** | 配置了 Neo4j/LLM API Key → 走真实服务（跟生产环境一致）；未配置 → 自动降级到本地模拟（networkx/numpy/规则引擎），**零门槛开箱即跑** |
| **JSON 数据契约** | 每步的输出写入 `shared_data/step_NN_xxx.json`，下一步读取——真实模拟微服务间数据传递 |
| **独立可跑** | 每个 demo 有独立的入口文件（如 `parse_document.py`）和 `core.py`，`cd` 进去直接运行 |
| **全链路编排** | demo_11 导入所有 demo 的 core，一键跑通全流程 |
| **代码即课件** | 源码与 `backend/app/services/` 同款架构——策略模式、Kahn算法、PromptEngineer 8段式、ChromaDB三路混合检索、LangGraph StateGraph |

## 流水线架构

```
sample_swagger.json
      │
      ▼
┌──────────────────────────────────────────────────────────┐
│  demo_02  文档解析    → step_02_interfaces.json          │
│  demo_03  知识图谱    → step_03_knowledge_graph.json     │
│  demo_04  依赖分析    → step_04_dependencies.json        │
│  demo_05  数据工厂    → step_05_test_data.json           │
│  demo_06  用例生成    → step_06_test_cases.json          │
│  demo_07  RAG知识库   → step_07_rag_index.json           │
│  demo_08  Agent编排   → step_08_agent_cases.json         │
│  demo_09  异步执行    → step_09_results.json             │
│  demo_10  失败分析    → step_10_analysis.json            │
└──────────────────────────────────────────────────────────┘
      │
      ▼
  demo_11  端到端编排 —— 一键跑通 10 步流水线
  demo_12  生产部署   —— docker-compose.yml（参考配置，不需要真实启动）
```

## 快速开始

### 零配置开箱即跑（本地降级模式）

```bash
# 无需任何配置，直接跑全链路
cd demo_11_端到端 && python run_pipeline.py
```

> 10步流水线 ~0.6s 跑完，输出结果在 `shared_data/step_*.json`

### 连接真实服务（生产模式）

创建 `.env` 文件在 `代码demo/` 目录：

```env
# Neo4j 图数据库
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password

# DeepSeek LLM
DEEPSEEK_API_KEY=sk-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# 通义千问 (Embedding + Reranker)
QWEN_API_KEY=sk-your-key

# 强制使用真实服务（跳过降级）
DEMO_MODE=force_real
```

### 安装可选依赖

```bash
# 基础依赖（本地降级模式必需）
pip install networkx numpy

# 真实服务依赖（按需安装）
pip install neo4j openai              # Neo4j + DeepSeek LLM
pip install chromadb dashscope rank-bm25  # ChromaDB + Embedding + BM25
pip install langgraph langchain-core  # LangGraph Agent编排
pip install faker                     # 真实数据生成
```

### 逐课运行

```bash
# 每一讲都可以独立运行
cd demo_02_文档解析 && python parse_document.py   # 6 个接口，开始流水线
cd demo_03_知识图谱 && python build_graph.py      # 构建依赖图
cd demo_04_依赖分析 && python analyze_deps.py     # Kahn 拓扑排序
cd demo_05_数据工厂 && python generate_data.py    # 21 组测试数据
cd demo_06_用例生成 && python generate_cases.py   # 21 条 pytest 用例
cd demo_07_RAG知识库 && python build_index.py     # 27 条文档向量化
cd demo_08_Agent编排 && python run_agent.py       # Agent 补充用例
cd demo_09_异步执行 && python execute_cases.py    # 并发执行 + Mock
cd demo_10_失败分析 && python analyze_results.py  # 三层分类诊断
```

### 一键全链路

```bash
cd demo_11_端到端 && python run_pipeline.py
```

输出示例：

```
======================================================================
  端到端全链路测试 — 10步流水线一键串联
======================================================================
  [INFO] Neo4j 不可用，使用 networkx 本地图模式
  [INFO] Faker: 未安装，使用内置生成器
  [INFO] 生成模式: 模板引擎 (降级)
  [INFO] 使用 numpy char n-gram 哈希向量: 60 个文档, 64 维
  [OK] Step 02 文档解析: 6 个接口
  [OK] Step 03 知识图谱: 6 节点, 13 边
  [OK] Step 04 依赖分析: 执行顺序 [登录, 创建设备, 查询列表, 查询详情, 更新设备, 删除设备]
  [OK] Step 05 数据工厂: 21 组测试数据
  [OK] Step 06 用例生成: 21 条 pytest 用例
  [OK] Step 07 RAG知识库: 60 条文档已索引
  [OK] Step 08 Agent编排: 3 条补充用例
  [OK] Step 09 异步执行: 12 pass / 9 fail (通过率 57.1%)
  [OK] Step 10 失败分析: 9 条需关注 (代码Bug 6, 数据问题 3)
  ----------------------------------------------------------------------
  总耗时: 0.64s
  状态: 流水线 10/10 步全部完成
```

## 核心共享模块: demo_common.py

所有 demo 的公共基础设施（~330行），对应 `backend/app/config.py` + `db_service.py` + `llm_service.py`：

| 模块 | 类/函数 | 功能 |
|:---|:---|:---|
| **Config** | `Config.NEO4J_URI`, `Config.DEEPSEEK_API_KEY`, `Config.is_llm_available()` 等 | 从 `.env` 文件加载配置，检查服务可用性 |
| **Neo4jManager** | `neo4j_manager.get_session()`, `is_available()` | Neo4j连接管理：Lock保护、延迟初始化、认证限流等待、自动降级 |
| **LLMClient** | `llm_client.chat()`, `extract_json()` | DeepSeek API封装：对话、JSON提取（支持 ```json/正则/find-rfind 三种提取策略） |
| **JSON工具** | `read_json()`, `write_json()`, `read_json_safe()` | 数据契约读写：自动注入_meta、友好的缺失提示 |
| **打印工具** | `print_header()`, `print_step()`, `print_summary()`, `print_service_status()` | 统一格式的步骤打印和服务状态报告 |

## 各 Demo 说明

### demo_01 概览
打印 10 步流水线 ASCII 架构图和数据契约对照表。纯展示，无代码逻辑，无依赖。

### demo_02 文档解析
**核心函数**: `parse_swagger_document(swagger_json) → List[APIInterface]`
- **策略模式**: parser_map 注册表，10+种格式独立解析（Swagger/Postman/Apifox/JMX/JSON/YAML/PDF/Word/Excel/CSV/Markdown/XML）
- **$ref 引用展开**: 递归 JSON Pointer 解引用，深度限制防死循环
- **Swagger paths 遍历**: 提取 method/path/service/headers/params/body/response_schema
- **三层路由**: 标准Swagger直接解析 → LLM辅助标准化（DeepSeek） → 规则引擎降级
- **CRUD推断**: POST→CREATE, GET→READ, PUT/PATCH→UPDATE, DELETE→DELETE
- **版本提取**: 正则匹配 `/v1/`, `/V0.1/`, `/api/v2/` 等URL模式
- 源码对应: `backend/app/services/enhanced_document_parser.py`

### demo_03 知识图谱
**核心函数**: `build_knowledge_graph(interfaces) → Dict`
- **双模存储**: Neo4j Cypher（MERGE节点+边） → networkx.DiGraph（自动降级）
- **节点**: APIInterface（10属性: name/method/path/service/crud_type/category/version等）
- **边**: DEPENDS_ON（data_flow + business_logic两种类型）
- **15种业务关系**: CONTAINS, OWNS, BINDS_TO, ASSOCIATES_WITH, CREATES, USES等
- **登录节点检测**: 自动识别auth/login/登录节点，作为图根节点
- **Token流**: login→所有需认证接口; **ID流**: CREATE→READ→UPDATE→DELETE
- 源码对应: `backend/app/services/db_service.py` (Neo4j部分)

### demo_04 依赖分析
**核心函数**: `analyze_dependencies(interfaces, kg_data) → Dict`
- **Kahn拓扑排序**: 入度表→零入度队列→BFS逐层剥离，O(V+E)时间复杂度
- **DFS三色环检测**: WHITE/GRAY/BLACK标记，精确定位环路径
- **32组业务分组**: 关键词匹配（phone_login/email/device/course/family/plan等）
- **版本隔离**: V0.1和V6严格分离，`SequenceMatcher`相似度聚类降级
- **三条边获取路径**: Neo4j Cypher → kg_data JSON → 启发式推断
- **数据流链路**: token_flow + id_flow + 注入目标映射
- 源码对应: `backend/app/services/optimized_dependency_analyzer.py` (3126行)

### demo_05 数据工厂
**核心函数**: `generate_test_data(interfaces, deps_data) → Dict`
- **四层数据模型**: L1原子数据(Faker) → L2接口组装 → L3 DAG上下文 → L4场景分类
- **23种字段映射**: phone/email/username/password/sn/device_id/mac/token/JWT/id_card等
- **4类用例数据**: positive(正常值) / boundary(边界值/空串/SQL注入/XSS) / negative(缺失字段) / invalid(全null)
- **DAG执行上下文**: token(login→下游) / device_id(create→read/update/delete) 追踪
- **语义推断**: 字段名含"phone"→手机号、"email"→邮箱、"sn"→SN前缀
- 源码对应: `backend/app/services/smart_test_data_generator.py`

### demo_06 用例生成
**核心函数**: `generate_test_cases(interfaces, test_data) → Dict`
- **8段式Prompt构建器**: ①角色定义 ②任务描述 ③API详情 ④测试数据 ⑤代码结构 ⑥代码规范 ⑦自定义要求 ⑧输出格式
- **双模生成**: LLM模式(DeepSeek + PromptEngineer) → 模板模式(pytest函数直出)
- **三种断言层**: 状态码 → JSON结构 → 字段类型
- **双格式输出**: pytest代码(requests+pytest) + HttpRunner YAML
- **conftest.py生成**: base_url fixture + auth_headers fixture + api_client fixture
- 源码对应: `backend/app/services/prompt_engineer.py` (377行) + `test_case_generator.py` (731行)

### demo_07 RAG知识库
**核心函数**: `build_rag_index(interfaces, test_cases) → Dict`
- **三路混合检索**: ChromaDB向量检索(语义) + BM25关键词检索(精确) + Reranker重排序(精排)
- **真·Embedding**: 通义千问 text-embedding-v3 → 1536维向量 → ChromaDB PersistentClient持久化
- **混合分数融合**: `final = vec×0.6 + bm25×0.2 + rerank×0.2`
- **降级方案**: char n-gram哈希 → 64维伪向量 → numpy余弦相似度
- **文档分块**: 接口概览/参数/响应/代码 四种chunk类型，细粒度检索
- **search_similar()**: 提供统一检索接口，被demo_08的Agent编排调用
- 源码对应: `backend/app/services/vector_service.py` (462行) + `rag_service.py` (159行) + `reranker_service.py` (66行)

### demo_08 Agent编排
**核心函数**: `run_agent_workflow(task_desc, rag_index, interfaces) → Dict`
- **LangGraph StateGraph**: add_node → set_entry_point → add_edge → compile → ainvoke
- **AgentState黑板架构**: 7字段共享状态（messages/task/parsed_interfaces/dependencies/test_cases/context/project_id）
- **三Agent协作**: InterfaceParserAgent → DependencyAnalyzerAgent → TestCaseGeneratorAgent
- **外层Workflow固定编排 + 内层Agent智能决策**: 工业界最务实的混合架构
- **RAG上下文注入**: 每个Agent执行前检索相关知识注入State
- **降级**: LangGraph不可用→顺序函数调用；LLM不可用→关键词规则匹配
- 源码对应: `backend/app/services/agent_service.py` (315行)

### demo_09 异步执行
**核心函数**: `execute_test_cases(test_cases, concurrency=3) → Dict`
- **执行流水线**: URL构建→变量替换({token}→真实值)→HTTP调用→响应解析→断言执行
- **MockResponder**: 6种响应处理器（login/create/list/delete/update/get-single）
- **刻意注入30% DELETE失败率**: 为demo_10提供分析素材
- **threading并发**: Worker线程池 + queue.Queue任务分发
- **执行上下文传递**: login token → 下游业务用例自动注入
- **5种断言类型**: status_code / equals / contains / jsonpath / response_time
- 源码对应: `backend/app/services/test_executor.py` (356行)

### demo_10 失败分析
**核心函数**: `analyze_failures(exec_results) → Dict`
- **LLM双路径架构**: 主路径(4类证据→Prompt→LLM诊断) + 兜底路径(7条if/elif规则分类)
- **三层分类**: L1环境问题(网络超时) → L2数据问题(Token过期/资源不存在) → L3代码Bug(断言错误/Schema变更)
- **四类证据收集**: request_data + response_data + assertions_result + error_message
- **置信度评分**: 环境0.7 / 数据0.85 / 代码Bug 0.9
- **P0/P1/P2优先级修复建议**: 含受影响用例列表和具体操作指南
- **JSON提取容错**: 正则从LLM自然语言响应中提取JSON（支持"好的，分析如下：{...}"前缀）
- 源码对应: `backend/app/services/failure_analyzer.py` (377行)

### demo_11 端到端
**全链路编排**：导入 demo_02~10 的 core，按流水线顺序执行，打印每步结果和总耗时。

### demo_12 部署
**生产部署参考配置**：docker-compose.yml（MySQL + Neo4j + MinIO + Backend + Celery Worker）。不需要真实启动，仅供学习参考。

## 双模架构对照

每个 demo 都实现了**两套代码路径**，根据服务可用性自动切换：

| 模块 | 真实服务模式 | 本地降级模式 | 切换条件 |
|:---|:---|:---|:---|
| **知识图谱** | Neo4j Cypher (MERGE/DELETE) | networkx.DiGraph | `NEO4J_URI` 是否配置 |
| **LLM 调用** | DeepSeek API (chat/completions) | 规则引擎 + 模板 | `DEEPSEEK_API_KEY` 是否配置 |
| **向量检索** | ChromaDB + text-embedding-v3 (1536维) + BM25 + Reranker | numpy char n-gram hash (64维) + 余弦相似度 | `QWEN_API_KEY` 是否配置 |
| **Agent编排** | LangGraph StateGraph (add_node/add_edge/compile/ainvoke) | 顺序函数调用 | `langgraph` 是否安装 |
| **数据生成** | Faker (zh_CN) 23种字段映射 | 内置字典 + random | `faker` 是否安装 |
| **异步执行** | threading.Thread + queue.Queue | 串行 for 循环 | `DEMO_MODE` 配置 |
| **失败分析** | LLM 4类证据→Prompt→JSON诊断 | 7条 if/elif 规则分类 | `DEEPSEEK_API_KEY` 是否配置 |

> **核心设计理念**: 降级路径使用**相同的数据结构和输出格式**——上游消费者完全感知不到当前用的是真实服务还是本地模拟。学员在自己的机器上 `pip install networkx numpy` 就能跑通全链路，部署到有 Neo4j+LLM 的环境后自动升级到生产模式。

## 文件结构

```
代码demo/
├── README.md                      ← 本文件
├── setup.sh / setup.ps1           ← 一键安装脚本 (Docker + Python)
├── docker-compose.yml             ← Docker 基础设施 (MySQL/Neo4j)
├── .env                           ← 连接配置 (API Keys + 服务端口)
├── requirements.txt               ← Python 依赖 (基础/推荐/可选)
├── init-mysql/init.sql            ← MySQL 建表脚本 (16张表)
├── init-neo4j/init.cypher         ← Neo4j 种子数据 (6节点+30+关系)
├── demo_common.py                 ← 公共工具（Config/LM/Neo4j/JSON读写）
├── shared_data/
│   ├── sample_swagger.json        ← 起点：6 个接口定义
│   ├── step_02_interfaces.json    ← demo_02 输出 → demo_03/04/05/07/08 输入
│   ├── step_03_knowledge_graph.json
│   ├── step_04_dependencies.json
│   ├── step_05_test_data.json
│   ├── step_06_test_cases.json
│   ├── step_07_rag_index.json
│   ├── step_08_agent_cases.json
│   ├── step_09_results.json
│   └── step_10_analysis.json
├── demo_01_概览/
│   ├── show_overview.py           ← 打印流水线架构图
│   └── pipeline.json              ← 流水线步骤定义
├── demo_02_文档解析/
│   ├── parse_document.py          ← 入口
│   └── core.py                    ← parse_swagger_document()
├── demo_03_知识图谱/
│   ├── build_graph.py             ← 入口
│   └── core.py                    ← build_knowledge_graph()
├── demo_04_依赖分析/
│   ├── analyze_deps.py            ← 入口
│   └── core.py                    ← analyze_dependencies()
├── demo_05_数据工厂/
│   ├── generate_data.py           ← 入口
│   └── core.py                    ← generate_test_data()
├── demo_06_用例生成/
│   ├── generate_cases.py          ← 入口
│   └── core.py                    ← generate_test_cases()
├── demo_07_RAG知识库/
│   ├── build_index.py             ← 入口
│   └── core.py                    ← build_rag_index(), search_similar()
├── demo_08_Agent编排/
│   ├── run_agent.py               ← 入口
│   └── core.py                    ← run_agent_workflow()
├── demo_09_异步执行/
│   ├── execute_cases.py           ← 入口
│   └── core.py                    ← execute_test_cases(), MockResponder
├── demo_10_失败分析/
│   ├── analyze_results.py         ← 入口
│   └── core.py                    ← analyze_failures()
├── demo_11_端到端/
│   └── run_pipeline.py            ← 全链路编排器
└── demo_12_部署/
    ├── README.md
    └── docker-compose.yml
```

## 与课件的关系

- **第 01 讲**（开篇全景）→ demo_01 展示流水线架构图
- **第 02 讲**（文档解析）→ demo_02 解析 Swagger JSON，生成标准 APIInterface
- **第 03 讲**（知识图谱）→ demo_03 用 networkx 构建依赖图
- **第 04 讲**（依赖分析）→ demo_04 Kahn 拓扑排序 + 数据流推断
- **第 05 讲**（数据工厂）→ demo_05 字段名语义推断 + 4 类用例数据
- **第 06 讲**（用例生成）→ demo_06 模板生成 pytest 代码
- **第 07 讲**（RAG知识库）→ demo_07 numpy 向量化 + 余弦检索
- **第 08 讲**（Agent编排）→ demo_08 3-Agent 规则工作流
- **第 09 讲**（异步执行）→ demo_09 threading 并发 + Mock 响应
- **第 10 讲**（失败分析）→ demo_10 三层分类 + 根因推断
- **第 11 讲**（端到端）→ demo_11 一键跑通全流程
- **第 12 讲**（生产部署）→ demo_12 docker-compose.yml

课件路径：`apitest/接口测试课件/重构版/`
