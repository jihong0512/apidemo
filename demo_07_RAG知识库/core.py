"""
demo_07 RAG知识库 -- 核心逻辑 (重构版 v3.0)
════════════════════════════════════════════════════════════════
对应课件: 第07讲 RAG知识库 -- 混合检索与图谱增强
后端源码参考:
  - backend/app/services/vector_service.py (462行) -- ChromaDB向量存储+Embedding生成
  - backend/app/services/rag_service.py (159行) -- 三层混合检索编排
  - backend/app/services/reranker_service.py (66行) -- Cross-Encoder重排序

架构: 三层混合检索
  ① ChromaDB 向量检索（语义相似） -- 通义千问 text-embedding-v3 (1536维)
  ② BM25 关键词检索（精确匹配） -- rank_bm25 Okapi算法
  ③ Reranker 重排序（Cross-Encoder精排） -- dashscope gte-rerank

服务可用时: ChromaDB + Qwen Embedding + BM25 + Reranker 全链路
服务不可用时: numpy char n-gram hashing → 64维伪向量 + 余弦相似度

设计要点 (from vector_service.py):
  - embed() 方法: 批量调用 dashscope.TextEmbedding, 线程池并发(最多5个), 批次大小10
  - search() 方法: 向量检索→BM25关键词→Reranker重排序 三级流水线
  - ChromaDB 持久化模式: PersistentClient + get_or_create_collection
  - 距离→相似度转换: score = 1.0 / (1.0 + distance)  (ChromaDB默认L2距离)
"""
import sys
from pathlib import Path

# 确保可以导入 demo_common
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_common import (
    add_project_to_sys_path, Config,
)
add_project_to_sys_path(__file__)

import hashlib
import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

# numpy 是可选依赖——不可用时降级到纯 Python 数学运算
try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    np = None  # type: ignore
    _NUMPY_AVAILABLE = False


# ══════════════════════════════════════════════════════════════
# NumPy 兼容层——numpy 不可用时提供纯 Python 替代
# ══════════════════════════════════════════════════════════════
def _np_zeros(shape, *args):
    """np.zeros 替代"""
    if isinstance(shape, tuple):
        if len(shape) == 0:
            return 0.0
        if len(shape) == 1:
            return [0.0] * shape[0]
        return [[0.0] * shape[1] for _ in range(shape[0])]
    return [0.0] * shape

def _np_array(data, *args, **kwargs):
    """np.array 替代——保持 list 原生结构"""
    return list(data)

def _np_linalg_norm(arr, axis=None, keepdims=False):
    """np.linalg.norm 替代——仅支持 L2 范数"""
    if axis is None:
        return math.sqrt(sum(x * x for x in arr if isinstance(x, (int, float)))
                         if isinstance(arr, list) else _np_linalg_norm(list(arr)))
    if axis == 1:
        result = [math.sqrt(sum(x * x for x in row if isinstance(x, (int, float))))
                  for row in arr]
        if keepdims:
            return [[r] for r in result]
        return result
    return []

def _np_dot(a, b):
    """np.dot 替代——向量点积"""
    if isinstance(a, list) and isinstance(b, list):
        return sum(x * y for x, y in zip(a, b))
    return 0.0

def _np_argsort(arr):
    """np.argsort 替代——返回排序后的索引列表"""
    indexed = list(enumerate(arr))
    indexed.sort(key=lambda x: x[1])
    return [i for i, _ in indexed]


# ── NumPy/纯Python 路由别名 —— 下游代码统一用 _xxx 前缀, 自动选择实现 ──
if _NUMPY_AVAILABLE:
    _zeros = np.zeros
    _array = np.array
    _linalg_norm = np.linalg.norm
    _dot = np.dot
    _argsort = np.argsort
else:
    _zeros = _np_zeros
    _array = _np_array
    _linalg_norm = _np_linalg_norm
    _dot = _np_dot
    _argsort = _np_argsort


def _get_dim(arr, axis=1):
    """获取数组维度——兼容 numpy array.shape 和纯 Python list"""
    if _NUMPY_AVAILABLE and hasattr(arr, 'shape'):
        return arr.shape[axis]
    if not arr:
        return 0
    if isinstance(arr[0], (list, tuple)):
        return len(arr[0])
    return len(arr)


def _row_normalize(matrix, norms_vec):
    """行归一化: matrix / norms_vec (兼容 numpy array 和纯 Python list)"""
    if _NUMPY_AVAILABLE:
        return np.array(matrix) / (np.array(norms_vec) + 1e-8)
    result = []
    for i, row in enumerate(matrix):
        n = norms_vec[i][0] if i < len(norms_vec) and norms_vec[i] else 1.0
        n = max(n, 1e-8)
        result.append([v / n for v in row])
    return result


def _div_scalar(arr, scalar):
    """数组除以标量 (兼容 numpy 和纯 Python)"""
    if _NUMPY_AVAILABLE:
        return np.array(arr) / max(scalar, 1e-8)
    d = max(scalar, 1e-8)
    return [v / d for v in arr]


def _weighted_sum(a, wa, b, wb):
    """a * wa + b * wb 逐元素 (兼容 numpy 和纯 Python)"""
    if _NUMPY_AVAILABLE:
        return np.array(a) * wa + np.array(b) * wb
    return [
        (a[i] if isinstance(a[i], (int, float)) else 0.0) * wa
        + (b[i] if i < len(b) and isinstance(b[i], (int, float)) else 0.0) * wb
        for i in range(len(a))
    ]


def _dot_rows(matrix, vec):
    """矩阵每行与向量点积 (兼容 np.dot(matrix, vec) 和纯 Python)"""
    if _NUMPY_AVAILABLE:
        return np.dot(np.array(matrix), np.array(vec))
    return [sum(row[j] * vec[j] for j in range(len(row))) for row in matrix]


# ══════════════════════════════════════════════════════════════
# 文档分块: 将接口描述 + 用例信息拆分为可索引的chunks
# 与 vector_service.py L232-308 add_classified_content() 同款逻辑:
#   按内容类型（接口/用例）分类存储，metadata标明来源
# ══════════════════════════════════════════════════════════════

def _chunk_interface(iface: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    将一个接口拆分为多个可索引的文本块

    为什么要分块?
      - LLM 上下文窗口有限, 全量文档塞不进去
      - 精准检索需要细粒度: 查询"登录的请求参数"不应返回整个接口文档
      - ChromaDB 每个 document 对应一个向量, 粒度越细检索精度越高
    """
    chunks = []
    base_meta = {
        "source": "interface",
        "name": iface.get("name", ""),
        "method": iface.get("method", ""),
        "url": iface.get("url", ""),
        "service": iface.get("service", ""),
        "tags": iface.get("tags", []),
    }

    # ① 接口概览块: method + url + name + description
    overview = f"[接口] {iface.get('method', '')} {iface.get('url', '')} - {iface.get('name', '')}"
    desc = iface.get("description", "")
    if desc:
        overview += f"\n描述: {desc}"
    chunks.append({"text": overview, "metadata": {**base_meta, "chunk_type": "overview"}})

    # ② 请求参数块: params + headers + body schema
    params_info = f"[请求参数] {iface.get('method', '')} {iface.get('url', '')}"
    if iface.get("params"):
        params_info += f"\nQuery参数: {json.dumps(iface['params'], ensure_ascii=False)}"
    if iface.get("headers"):
        params_info += f"\nHeaders: {json.dumps(iface['headers'], ensure_ascii=False)}"
    body = iface.get("body", {})
    if body and body.get("schema"):
        params_info += f"\n请求体: {json.dumps(body['schema'], ensure_ascii=False)}"
    if params_info != f"[请求参数] {iface.get('method', '')} {iface.get('url', '')}":
        chunks.append({"text": params_info, "metadata": {**base_meta, "chunk_type": "params"}})

    # ③ 响应结构块
    response = iface.get("response_schema", {})
    if response:
        resp_text = f"[响应结构] {iface.get('method', '')} {iface.get('url', '')}\n{json.dumps(response, ensure_ascii=False)}"
        chunks.append({"text": resp_text, "metadata": {**base_meta, "chunk_type": "response"}})

    return chunks


def _chunk_test_case(tc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """将测试用例拆分为索引块"""
    base_meta = {
        "source": "test_case",
        "function_name": tc.get("function_name", ""),
        "interface_name": tc.get("interface_name", ""),
        "method": tc.get("method", ""),
        "case_type": tc.get("case_type", ""),
    }

    # 用例信息摘要
    summary = (
        f"[测试用例] {tc.get('function_name', '')} "
        f"接口: {tc.get('interface_name', '')} "
        f"方法: {tc.get('method', '')} "
        f"类型: {tc.get('case_type', '')} "
        f"期望状态码: {tc.get('expected_status', '')}"
    )
    chunks = [{"text": summary, "metadata": {**base_meta, "chunk_type": "summary"}}]

    # pytest代码片段（前300字符）
    code = tc.get("pytest_code", "")
    if code:
        # 截取关键部分: 跳过函数定义头, 聚焦请求/断言逻辑
        code_text = f"[用例代码] {tc.get('function_name', '')}\n{code[:300]}"
        chunks.append({"text": code_text, "metadata": {**base_meta, "chunk_type": "code"}})

    return chunks


# ══════════════════════════════════════════════════════════════
# 降级方案: char n-gram hashing → 伪向量 (与现有实现同款)
# 当 QWEN API 不可用时, 用确定性哈希模拟 Embedding
# 为什么用 char 3-gram 而不是 word-level?
#   - 中文无空格分词, word-level 分词器不稳定
#   - char n-gram 对拼写错误/中英文混合有天然的容错性
# ══════════════════════════════════════════════════════════════

def _hash_vector(text: str, dim: int = 64):
    """
    字符 n-gram hashing → 伪嵌入向量

    与现有的 _text_to_vector() 逻辑一致, 但增加了:
      - 位置编码: 越靠前的n-gram权重越高 (模拟TF-IDF的位置假设)
      - 混合粒度: char 3-gram + bigram 双重哈希

    Returns: list (numpy不可用时) 或 np.ndarray (numpy可用时)
    """
    vec = _zeros(dim)
    text_len = len(text)
    if text_len == 0:
        # 零向量归一化后返回
        if _NUMPY_AVAILABLE:
            return np.array(vec) / (np.linalg.norm(vec) + 1e-8)
        else:
            return [v / (math.sqrt(sum(x*x for x in vec)) + 1e-8) for v in vec]

    # char-level 3-gram (主要特征)
    for i in range(text_len - 2):
        trigram = text[i:i + 3]
        h = int(hashlib.md5(trigram.encode()).hexdigest(), 16)
        # 位置衰减: 文本前半部分的n-gram权重更高
        pos_weight = 1.0 - 0.3 * (i / max(text_len - 2, 1))
        vec[h % dim] += pos_weight

    # char-level 2-gram (补充特征, 捕获短词)
    for i in range(text_len - 1):
        bigram = text[i:i + 2]
        h = int(hashlib.md5(bigram.encode()).hexdigest(), 16)
        vec[h % dim] += 0.3  # 低权重补充

    # word-level (英文/拼音场景)
    words = text.lower().split()
    for word in words:
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        vec[h % dim] += 0.5

    # L2归一化 → 余弦相似度可直接用点积计算
    if _NUMPY_AVAILABLE:
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = np.array(vec) / norm
    else:
        norm_val = math.sqrt(sum(x * x for x in vec))
        if norm_val > 0:
            vec = [v / norm_val for v in vec]

    return vec


def _short_hash(text: str) -> str:
    """生成短哈希ID"""
    return hashlib.md5(text.encode()).hexdigest()[:8]


def _hash_embedding(text: str) -> list:
    """Hash embedding 降级：将文本转为固定长度的 hash 向量"""
    return _hash_vector(text, dim=64)


# ══════════════════════════════════════════════════════════════
# BM25 Okapi 关键词检索 (from reranker_service + vector_service)
# BM25 是经典的 TF-IDF 的概率改进版
# 为什么向量检索之外还需要 BM25?
#   - Embedding 对专有名词/ID/错误码的精确匹配能力弱
#   - "device_id=42" 这类精确查询, BM25 比向量检索更准
#   - 中英文混合场景下, Embedding 模型可能"猜偏"语义
# ══════════════════════════════════════════════════════════════

def _bm25_search(query: str, documents: List[str]) -> List[float]:
    """
    BM25 Okapi 关键词检索 (from vector_service.py L381-392)

    返回每个文档的 BM25 分数, 分数越高越相关
    """
    if not documents:
        return []

    try:
        from rank_bm25 import BM25Okapi
        tokenized_corpus = [doc.split() for doc in documents]
        bm25 = BM25Okapi(tokenized_corpus)
        scores = bm25.get_scores(query.split())
        return [float(s) for s in scores]
    except ImportError:
        # rank_bm25 未安装 → 降级为简单的词频重叠
        query_words = set(query.lower().split())
        scores = []
        for doc in documents:
            doc_words = set(doc.lower().split())
            overlap = len(query_words & doc_words)
            scores.append(float(overlap) / max(len(query_words), 1))
        return scores


# ══════════════════════════════════════════════════════════════
# ChromaDB + Qwen Embedding 真实实现 (from vector_service.py)
# 对应 vector_service.py L82-162 embed() 方法
# ══════════════════════════════════════════════════════════════

def _real_embed(texts: List[str]) -> List[List[float]]:
    """
    使用通义千问 text-embedding-v3 生成向量 (1536维)

    与 vector_service.py embed() 同款:
      - 批量处理 (batch_size=10)
      - 调用 dashscope.TextEmbedding
      - 失败时返回零向量
    """
    if not texts:
        return []

    try:
        import dashscope
        from dashscope import TextEmbedding

        dashscope.api_key = Config.QWEN_API_KEY

        all_embeddings = []
        batch_size = 10

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            try:
                resp = TextEmbedding.call(
                    model=Config.EMBEDDING_MODEL,
                    input=batch
                )
                if resp.status_code == 200:
                    output = resp.get("output", {})
                    embeddings_data = output.get("embeddings", [])
                    if embeddings_data:
                        for item in embeddings_data:
                            all_embeddings.append(item.get("embedding", [0.0] * 1536))
                    else:
                        all_embeddings.extend([[0.0] * 1536] * len(batch))
                else:
                    print(f"  [WARN] Embedding API失败: {resp.status_code} - {resp.message}")
                    all_embeddings.extend([[0.0] * 1536] * len(batch))
            except Exception as e:
                print(f"  [WARN] Embedding批次失败: {e}")
                all_embeddings.extend([[0.0] * 1536] * len(batch))

        return all_embeddings
    except ImportError:
        print("  [WARN] dashscope 未安装, Embedding 不可用")
        return []


# ══════════════════════════════════════════════════════════════
# Reranker 重排序 (from reranker_service.py)
# 对应 reranker_service.py L8-65 RerankerService.rerank()
# 为什么需要重排序?
#   - 向量检索返回宽泛的相关结果, BM25返回精确匹配
#   - Reranker (Cross-Encoder) 将 query 和 document 拼接后编码
#   - 这种联合编码能捕获更细粒度的语义匹配信号
#   - 代价是速度慢 (O(n) 次编码), 所以只对向量检索的 top_k*2 候选做重排
# ══════════════════════════════════════════════════════════════

def _rerank(query: str, texts: List[str]) -> List[Tuple[int, float]]:
    """
    使用通义千问 gte-rerank 进行重排序

    与 reranker_service.py rerank() 同款:
      - 调用 dashscope.TextReRank
      - 返回 (原始索引, 相关性分数) 列表 (按分数降序)
      - 失败时返回原始顺序
    """
    if not texts:
        return []

    try:
        import dashscope
        from dashscope import TextReRank

        dashscope.api_key = Config.QWEN_API_KEY

        resp = TextReRank.call(
            model=Config.RERANKER_MODEL,
            query=query,
            documents=texts
        )

        if resp.status_code == 200:
            output = resp.get("output", {})
            results_data = output.get("results", [])
            if results_data:
                reranked = [(item["index"], item["relevance_score"]) for item in results_data]
                reranked.sort(key=lambda x: x[1], reverse=True)
                return reranked

        # API失败或返回空 → 返回原始顺序
        print(f"  [WARN] Reranker API返回空或不成功 (status={resp.status_code})")
        return [(i, 1.0) for i in range(len(texts))]

    except ImportError:
        return [(i, 1.0) for i in range(len(texts))]
    except Exception as e:
        print(f"  [WARN] Reranker失败: {e}")
        return [(i, 1.0) for i in range(len(texts))]


# ══════════════════════════════════════════════════════════════
# 核心: build_rag_index() -- 构建 RAG 知识库索引
# 流程:
#   1. 文档分块: 将接口+用例拆分为可索引的文本块
#   2. 根据服务可用性选择路径:
#      路径A (Qwen可用): ChromaDB + 真实1536维Embedding
#      路径B (降级):     numpy char n-gram hashing 64维伪向量
#   3. 建立检索基础结构 (documents + vectors 矩阵)
# ══════════════════════════════════════════════════════════════

def build_rag_index(
    interfaces: List[Dict[str, Any]],
    test_cases: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    构建 RAG 知识库索引

    三层混合检索架构 (from lecture 07):
      ① ChromaDB 向量检索（语义相似）
      ② BM25 关键词检索（精确匹配）
      ③ Reranker 重排序（Cross-Encoder 精排）

    Args:
        interfaces: 接口列表 (from step_02_interfaces.json)
        test_cases: 测试用例列表 (from step_06_test_cases.json)
    Returns:
        Dict: {
            documents_indexed, embedding_dimension,
            index_type ("chromadb+qwen"|"numpy+hash"),
            search_demo (样本查询结果)
        }
    """
    # ── 步骤1: 文档分块 ──
    # 将每个接口/用例拆成多个 chunk, 每个 chunk 独立索引
    # 这样检索时返回的是精确的文本片段, 而不是整个接口文档
    documents = []
    all_texts = []

    for iface in interfaces:
        chunks = _chunk_interface(iface)
        for chunk in chunks:
            doc_id = f"iface_{_short_hash(chunk['text'])}"
            documents.append({
                "doc_id": doc_id,
                "type": "interface",
                "text": chunk["text"],
                "metadata": chunk["metadata"],
            })
            all_texts.append(chunk["text"])

    for tc in test_cases:
        chunks = _chunk_test_case(tc)
        for chunk in chunks:
            doc_id = f"case_{_short_hash(chunk['text'])}"
            documents.append({
                "doc_id": doc_id,
                "type": "test_case",
                "text": chunk["text"],
                "metadata": chunk["metadata"],
            })
            all_texts.append(chunk["text"])

    # ── 步骤2: 选择索引路径 ──
    # 为什么优先 ChromaDB?
    #   - 持久化存储, 重启不丢失
    #   - 内置 ANN (近似最近邻) 索引, 百万级文档也能亚秒级检索
    #   - 支持 metadata 过滤 (如按接口类型过滤)
    use_chromadb = False
    embedding_dim = 64  # 降级方案的默认维度
    index_type = "numpy+hash"
    vectors = []
    collection = None  # 防止 _chromadb_collection_name 引用未定义变量

    if Config.is_qwen_available():
        # 路径A: 尝试 ChromaDB + 真实 Embedding
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            # 确保持久化目录存在
            persist_dir = Config.CHROMA_PERSIST_DIR
            Path(persist_dir).mkdir(parents=True, exist_ok=True)

            # 连接 ChromaDB (from vector_service.py L47-53)
            client = chromadb.PersistentClient(
                path=persist_dir,
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )

            # 获取或创建集合 (from vector_service.py L66-80)
            collection_name = "api_documents"
            try:
                collection = client.get_collection(name=collection_name)
                # 清空旧数据, 重新构建索引
                existing = collection.get()
                if existing and existing.get("ids"):
                    collection.delete(ids=existing["ids"])
            except Exception:
                collection = client.create_collection(
                    name=collection_name,
                    metadata={"description": "API文档向量集合"}
                )

            # 生成真实 Embeddings (1536维)
            print("  [INFO] 使用通义千问 text-embedding-v3 生成向量 (1536维)...")
            vectors = _real_embed(all_texts)

            if vectors and len(vectors) == len(all_texts):
                # 写入 ChromaDB (from vector_service.py L164-230)
                ids = [doc["doc_id"] for doc in documents]
                metadatas = []
                for doc in documents:
                    clean_meta = {}
                    for k, v in doc["metadata"].items():
                        if isinstance(v, (str, int, float, bool, type(None))):
                            clean_meta[k] = v
                        else:
                            clean_meta[k] = json.dumps(v, ensure_ascii=False)
                    metadatas.append(clean_meta)

                collection.add(
                    ids=ids,
                    embeddings=vectors,
                    documents=[doc["text"] for doc in documents],
                    metadatas=metadatas
                )

                embedding_dim = 1536
                index_type = "chromadb+qwen"
                use_chromadb = True
                print(f"  [OK] ChromaDB 索引已构建: {len(ids)} 个文档, {embedding_dim} 维")
            else:
                print("  [WARN] Embedding 生成不完整, 降级到 numpy 哈希向量")
        except ImportError as e:
            print(f"  [WARN] chromadb 未安装: {e}")
            print("  → 降级到 numpy 哈希向量 (pip install chromadb)")
        except Exception as e:
            print(f"  [WARN] ChromaDB 初始化失败: {e}")
            print("  → 降级到 numpy 哈希向量")

    # 路径B: numpy char n-gram hashing (降级方案)
    if not use_chromadb or len(vectors) != len(all_texts):
        index_type = "numpy+hash"
        embedding_dim = 64
        vectors = []
        for text in all_texts:
            vec = _hash_vector(text, dim=embedding_dim)
            # numpy可用时 vec 是 ndarray→转list; 不可用时已是list
            vectors.append(vec.tolist() if _NUMPY_AVAILABLE and hasattr(vec, 'tolist') else list(vec))
        print(f"  [INFO] 使用 numpy char n-gram 哈希向量: {len(vectors)} 个文档, {embedding_dim} 维")

    # ── 步骤3: 构建向量矩阵 (用于降级检索, 兼容 numpy/list) ──
    if _NUMPY_AVAILABLE:
        vector_matrix = np.array(vectors) if vectors else np.zeros((0, embedding_dim))
    else:
        vector_matrix = list(vectors) if vectors else []

    # ── 步骤4: 样本查询演示 ──
    sample_queries = ["用户登录接口", "创建设备", "删除操作", "获取列表"]
    search_demo = []
    for q in sample_queries[:2]:  # 只演示前2个, 避免输出过长
        results = _search_internal(q, documents, vector_matrix, top_k=2)
        search_demo.append({"query": q, "results": results})

    return {
        # ── 保持向后兼容的字段 (entry文件通过 print_summary 遍历) ──
        "documents": documents,
        "vectors": vector_matrix.tolist() if _NUMPY_AVAILABLE and hasattr(vector_matrix, 'tolist') else vector_matrix,
        "vector_dim": embedding_dim,
        "total_documents": len(documents),
        "by_type": {
            "interfaces": sum(1 for d in documents if d["type"] == "interface"),
            "test_cases": sum(1 for d in documents if d["type"] == "test_case"),
        },
        # ── 新的规范字段 ──
        "documents_indexed": len(documents),
        "embedding_dimension": embedding_dim,
        "index_type": index_type,
        "search_demo": search_demo,
        # ChromaDB 状态（供下游读取，不加 _ 前缀因为需要持久化到 JSON）
        "chromadb_collection_name": collection.name if use_chromadb and hasattr(collection, 'name') else None,
        "use_chromadb": use_chromadb,
    }


# ══════════════════════════════════════════════════════════════
# 内部检索实现 (供 build_rag_index 内 search_demo 调用)
# ══════════════════════════════════════════════════════════════

def _search_internal(
    query: str,
    documents: List[Dict[str, Any]],
    vector_matrix,
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """内部检索: 向量相似度 + BM25 + 加权融合"""
    if (hasattr(vector_matrix, '__len__') and len(vector_matrix) == 0) or vector_matrix is None:
        return []

    texts = [doc["text"] for doc in documents]

    # ① 向量相似度 (余弦相似度)
    query_vec = _hash_vector(query, dim=_get_dim(vector_matrix))
    q_norm = _linalg_norm(query_vec) + 1e-8
    if _NUMPY_AVAILABLE:
        query_vec = np.array(query_vec) / q_norm
        norms = np.linalg.norm(vector_matrix, axis=1, keepdims=True) + 1e-8
        normalized = np.array(vector_matrix) / norms
        vec_scores = np.dot(normalized, query_vec)
    else:
        query_vec = [v / q_norm for v in query_vec]
        normalized = _row_normalize(vector_matrix, _linalg_norm(vector_matrix, axis=1, keepdims=True))
        vec_scores = _dot_rows(normalized, query_vec)

    # ② BM25 关键词分数
    bm25_scores = _bm25_search(query, texts)
    bm25_arr = _zeros(len(texts))
    for i, s in enumerate(bm25_scores):
        if i < len(bm25_arr):
            bm25_arr[i] = s
    # 归一化 BM25 到 [0, 1]
    if _NUMPY_AVAILABLE:
        if bm25_arr.max() > 0:
            bm25_arr = bm25_arr / bm25_arr.max()
    else:
        bm25_max = max(bm25_arr) if bm25_arr else 0
        if bm25_max > 0:
            bm25_arr = [s / bm25_max for s in bm25_arr]

    # ③ 混合分数融合 (from vector_service.py L388-392)
    #   vector_score * 0.6 + bm25_score * 0.2 + (rerank留20%)
    #   但内部检索暂不做rerank (too expensive for demo), 调整为:
    final_scores = _weighted_sum(vec_scores, 0.7, bm25_arr, 0.3)

    # 取 top_k
    top_indices = _argsort(final_scores)[::-1][:top_k]

    results = []
    for idx in top_indices:
        score = float(final_scores[idx])
        if score > 0.05:  # 最低相似度阈值
            results.append({
                "document": documents[idx],
                "similarity": round(score, 4),
                "vec_score": round(float(vec_scores[idx]), 4),
                "bm25_score": round(float(bm25_arr[idx]), 4) if idx < len(bm25_arr) else 0.0,
            })

    return results


# ══════════════════════════════════════════════════════════════
# 公开检索API: search_similar()
# 供 demo_08 Agent编排 调用, 注入RAG上下文
#
# 与 vector_service.py search() (L329-414) 同款三级流水线:
#   ChromaDB向量检索 → BM25关键词 → Reranker重排序 → 融合打分
# ══════════════════════════════════════════════════════════════

def search_similar(
    query: str,
    rag_index: Dict[str, Any],
    top_k: int = 3
) -> List[Dict[str, Any]]:
    """
    混合检索: 查询与 query 最相关的文档

    三级流水线 (from vector_service.py L329-414):
      1. 向量检索: 语义相似度
      2. BM25 关键词: 精确匹配
      3. Reranker 重排序: Cross-Encoder 精排 (Qwen API 可用时)

    混合分数融合公式:
      final_score = vector_score * 0.6 + bm25_score * 0.2 + rerank_score * 0.2

    Args:
        query: 查询文本 (中文/英文混合都支持)
        rag_index: build_rag_index() 的返回值
        top_k: 返回前 K 个结果
    Returns:
        [{"document": {...}, "similarity": float, "vec_score": float, "bm25_score": float}, ...]
    """
    documents = rag_index.get("documents", [])
    vector_data = rag_index.get("vectors", [])

    if not documents or not vector_data:
        return []

    vectors = _array(vector_data)
    texts = [doc["text"] for doc in documents]
    vec_dim = _get_dim(vectors)

    # ── 层级1: 向量检索 ──
    # 生成查询向量
    if rag_index.get("use_chromadb") and Config.is_qwen_available():
        # ChromaDB路径: 尝试用真实Embedding
        query_embed = _real_embed([query])
        if query_embed:
            query_vec = _array(query_embed[0])
        else:
            query_vec = _hash_vector(query, dim=vec_dim)
    else:
        # 降级路径: hash向量
        query_vec = _hash_vector(query, dim=vec_dim)

    # 查询向量归一化
    q_norm = _linalg_norm(query_vec) + 1e-8
    if _NUMPY_AVAILABLE:
        query_vec = np.array(query_vec) / q_norm
        norms = np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-8
        normalized = np.array(vectors) / norms
        vec_scores = np.dot(normalized, query_vec)
    else:
        query_vec = [v / q_norm for v in query_vec]
        normalized = _row_normalize(vectors, _linalg_norm(vectors, axis=1, keepdims=True))
        vec_scores = _dot_rows(normalized, query_vec)

    # ── 层级2: BM25 关键词检索 ──
    bm25_scores = _bm25_search(query, texts)
    bm25_arr = _zeros(len(texts))
    for i, s in enumerate(bm25_scores):
        if i < len(bm25_arr):
            bm25_arr[i] = s
    if _NUMPY_AVAILABLE:
        if bm25_arr.max() > 0:
            bm25_arr = bm25_arr / bm25_arr.max()
    else:
        bm25_max = max(bm25_arr) if bm25_arr else 0
        if bm25_max > 0:
            bm25_arr = [s / bm25_max for s in bm25_arr]

    # ── 层级3: Reranker 重排序 ──
    # 先用向量+BM25混合取 top_k*2 候选, 再对候选做rerank
    vec_bm25 = _weighted_sum(vec_scores, 0.7, bm25_arr, 0.3)
    candidate_count = min(top_k * 3, len(texts))
    candidate_indices = _argsort(vec_bm25)[::-1][:candidate_count]

    candidate_texts = [texts[i] for i in candidate_indices]

    if Config.is_qwen_available() and len(candidate_texts) > 1:
        reranked = _rerank(query, candidate_texts)
        # 混合分数融合: final_score = vector_score * 0.6 + bm25_score * 0.2 + rerank_score * 0.2
        final_scores = {}
        for rank_idx, (orig_idx_pos, rerank_score) in enumerate(reranked):
            if orig_idx_pos < len(candidate_indices):
                real_idx = candidate_indices[orig_idx_pos]
                vec_s = max(0, float(vec_scores[real_idx]))
                bm25_s = float(bm25_arr[real_idx]) if real_idx < len(bm25_arr) else 0
                final_scores[real_idx] = vec_s * 0.6 + bm25_s * 0.2 + rerank_score * 0.2
                # 同时记录rerank后的混合排名位置
                final_scores[f"__rank_{real_idx}"] = rank_idx

        # 按最终分数排序 (int 兼容 numpy integer 和 Python int)
        sorted_indices = sorted(final_scores.keys(),
                               key=lambda k: final_scores[k] if isinstance(k, int) else -1,
                               reverse=True)
        sorted_indices = [i for i in sorted_indices if isinstance(i, int)]
    else:
        # 无Reranker → 直接用向量+BM25混合分数
        sorted_indices = _argsort(vec_bm25)[::-1]
        final_scores = {int(i): float(vec_bm25[i]) for i in sorted_indices}

    # ── 组装结果 ──
    results = []
    for idx in sorted_indices:
        idx = int(idx)
        if idx >= len(documents):
            continue
        score = final_scores.get(idx, float(vec_bm25[idx]))
        if score > 0.05:
            results.append({
                "document": documents[idx],
                "similarity": round(score, 4),
                "vec_score": round(float(vec_scores[idx]), 4),
                "bm25_score": round(float(bm25_arr[idx]), 4) if idx < len(bm25_arr) else 0.0,
            })
        if len(results) >= top_k:
            break

    return results


# ════════════════════════════════════════════════════════════════
# 图谱增强 RAG — Neo4j + 向量检索双路融合
#   (backend/rag_service.py L50-130 同款)
#
#   为什么需要图谱增强？纯向量检索只能找到"语义相似"的内容，
#   但无法理解接口间的依赖关系。比如查询"token 相关接口"，
#   向量检索可能漏掉那些 Authorization header 中含 token 但名字不相关的接口。
#   图谱路径从 Neo4j 的 DEPENDS_ON 关系补全这些遗漏。
#
#   双路融合策略:
#     向量路: ChromaDB 语义检索 → top_k 结果
#     图谱路: Neo4j Cypher 查询 → 依赖链上下文
#     融合: 图谱结果作为附加上下文注入，不覆盖向量结果
# ════════════════════════════════════════════════════════════════

def graph_rag_search(
    query: str,
    collection=None,
    top_k: int = 5,
    neo4j_session=None
) -> List[Dict]:
    """
    图谱增强 RAG——向量检索 + Neo4j 依赖图双路融合。

    Args:
        query: 用户查询文本
        collection: ChromaDB collection (可选)
        top_k: 向量检索返回数
        neo4j_session: Neo4j session (可选)

    Returns: [{document, similarity, source: "vector"|"graph", context: {...}}]
    """
    results = []
    # 从 collection 参数中提取 documents（demo 环境中 collection 可能是 rag_index dict）
    documents = collection.get("documents", []) if isinstance(collection, dict) else []

    # —— 路径1: 向量检索 ——
    if collection is not None:
        try:
            vec_results = search_similar(query, {"documents": documents}, top_k=top_k) if documents else []
            for r in vec_results:
                r["source"] = "vector"
                r["context"] = None
            results.extend(vec_results)
        except Exception as e:
            print(f"  [WARN] 向量检索失败: {e}")

    # —— 路径2: 图谱检索 ——
    graph_results = _graph_search(query, neo4j_session, top_k)
    for r in graph_results:
        r["source"] = "graph"
        r["similarity"] = 0.5  # 图谱结果无相似度分数，给默认中值
    results.extend(graph_results)

    # —— 去重 + 排序 (向量结果优先) ——
    seen = set()
    deduped = []
    for r in results:
        key = r.get("document", "")[:100]
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    # 向量结果排前面 (source="vector" 优先)
    deduped.sort(key=lambda r: (0 if r["source"] == "vector" else 1, -(r.get("similarity", 0))))
    return deduped[:top_k * 2]


def _graph_search(query: str, session, top_k: int = 5) -> List[Dict]:
    """
    从 Neo4j 图谱中搜索相关接口和依赖关系。

    策略: 根据查询关键词匹配接口节点 → 返回节点信息 + 1-hop 依赖
    """
    if session is None:
        return []

    results = []
    q_lower = query.lower()

    try:
        keywords = [w for w in re.findall(r'[一-鿿\w]+', q_lower)
                    if len(w) > 1 and w not in ('接口', '查询', '搜索', '检索', '相关')]
        if not keywords:
            keywords = [q_lower[:10]]

        for kw in keywords[:3]:
            cypher = """
                MATCH (api:APIInterface {demo_source: 'demo_03'})
                WHERE toLower(api.name) CONTAINS $kw
                   OR toLower(api.path) CONTAINS $kw
                   OR toLower(api.description) CONTAINS $kw
                OPTIONAL MATCH (api)-[r:DEPENDS_ON]->(dep:APIInterface {demo_source: 'demo_03'})
                RETURN api.name AS name, api.method AS method, api.path AS path,
                       api.service AS service, api.crud_type AS crud_type,
                       api.description AS description,
                       collect(DISTINCT {name: dep.name, type: type(r)}) AS dependencies
                LIMIT $limit
            """
            result = session.run(cypher, kw=kw, limit=top_k)
            for rec in result:
                doc_text = f"[{rec.get('service', '')}] {rec.get('method', 'GET')} {rec.get('path', '')} - {rec.get('name', '')}"
                deps = rec.get('dependencies', [])
                dep_text = ''
                if deps and deps[0] and deps[0].get('name'):
                    dep_names = [d.get('name', '') for d in deps if d and d.get('name')]
                    dep_text = f" (依赖: {', '.join(dep_names[:3])})"

                results.append({
                    "document": f"{doc_text}{dep_text}",
                    "api_name": rec.get("name", ""),
                    "method": rec.get("method", ""),
                    "path": rec.get("path", ""),
                    "service": rec.get("service", ""),
                    "crud_type": rec.get("crud_type", ""),
                    "dependencies": [d for d in deps if d and d.get('name')],
                    "source": "graph",
                })
    except Exception as e:
        print(f"  [WARN] 图谱搜索失败: {e}")

    return results[:top_k]


# ════════════════════════════════════════════════════════════════
# ThreadPoolExecutor 并发 Embedding 生成
#   (backend/vector_service.py L180-250 同款)
#
#   为什么并发？通义千问 text-embedding-v3 单次调用最多 25 个文本。
#   当文档分块 > 25 时，需多次调用。串行调用 → 每次 200-500ms，
#   100 个 chunk 需要 20-50 秒。ThreadPoolExecutor 5 个 worker 并发
#   可将总时间缩短到 5-10 秒 (4-5x 加速)。
#
#   批次设计: 每批 25 个文本 (API 限制) → 5 个 worker 各取一批并发调用
# ════════════════════════════════════════════════════════════════

def embed_documents_concurrent(
    documents: List[Dict],
    collection=None,
    max_workers: int = 5,
    batch_size: int = 25,
) -> List[Dict]:
    """
    ThreadPoolExecutor 并发生成 Embedding 并写入 ChromaDB。

    Args:
        documents: 文档列表 [{id, text, metadata}]
        collection: ChromaDB collection (可选，None 则只返回不写入)
        max_workers: 并发 worker 数
        batch_size: 每批文本数 (API 限制 25)

    Returns: [{id, text, embedding, metadata}] 含 embedding 结果的文档列表
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not documents:
        return []

    if len(documents) < batch_size:
        return _embed_batch_serial(documents, collection)

    print(f"  [INFO] 并发 Embedding: {len(documents)} 文档, {max_workers} workers, 批次大小 {batch_size}")

    batches = [documents[i:i+batch_size] for i in range(0, len(documents), batch_size)]
    results = [None] * len(batches)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(batches))) as executor:
        futures = {}
        for batch_idx, batch in enumerate(batches):
            future = executor.submit(_embed_single_batch, batch, batch_idx, collection)
            futures[future] = batch_idx

        for future in as_completed(futures):
            batch_idx = futures[future]
            try:
                results[batch_idx] = future.result()
            except Exception as e:
                print(f"  [ERROR] 批次 {batch_idx} Embedding 失败: {e}")
                results[batch_idx] = batch

    flat_results = []
    for r in results:
        if r:
            flat_results.extend(r)

    print(f"  [INFO] 并发 Embedding 完成: {len(flat_results)} 文档")
    return flat_results


def _embed_batch_serial(documents: List[Dict], collection=None) -> List[Dict]:
    """串行生成 Embedding (文档数 < 25 时使用)"""
    texts = [d["text"] for d in documents]
    embeddings = []

    if Config.is_qwen_available():
        try:
            embeddings = _generate_embeddings_via_qwen(texts)
        except Exception as e:
            print(f"  [WARN] Qwen Embedding 失败，使用 numpy 哈希降级: {e}")

    if not embeddings:
        embeddings = [_hash_embedding(t) for t in texts]

    if collection is not None and embeddings:
        ids = [d.get("id", f"doc_{i}") for i, d in enumerate(documents)]
        metadatas = [d.get("metadata", {}) for d in documents]
        try:
            collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
        except Exception as e:
            print(f"  [WARN] ChromaDB 写入失败: {e}")

    for i, doc in enumerate(documents):
        doc["embedding"] = embeddings[i] if i < len(embeddings) else []

    return documents


def _embed_single_batch(batch: List[Dict], batch_idx: int, collection=None) -> List[Dict]:
    """单批次 Embedding 生成 (worker 线程内执行)"""
    texts = [d["text"] for d in batch]
    ids = [d.get("id", f"doc_{batch_idx}_{i}") for i, d in enumerate(batch)]
    metadatas = [d.get("metadata", {}) for d in batch]

    embeddings = []
    if Config.is_qwen_available():
        try:
            embeddings = _generate_embeddings_via_qwen(texts)
        except Exception as e:
            print(f"  [WARN] 批次 {batch_idx} Qwen 降级到 numpy: {e}")

    if not embeddings:
        embeddings = [_hash_embedding(t) for t in texts]

    if collection is not None and embeddings:
        try:
            collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
        except Exception as e:
            print(f"  [WARN] 批次 {batch_idx} ChromaDB 写入失败: {e}")

    for i, doc in enumerate(batch):
        doc["embedding"] = embeddings[i] if i < len(embeddings) else []

    return batch


def _generate_embeddings_via_qwen(texts: List[str]) -> List[List[float]]:
    """通过通义千问 API 生成 Embedding。"""
    try:
        import dashscope
        from dashscope import TextEmbedding
    except ImportError:
        print("  [WARN] dashscope 未安装，无法生成 Embedding")
        return []

    api_key = Config.QWEN_API_KEY
    if not api_key:
        print("  [WARN] QWEN_API_KEY 未配置")
        return []

    try:
        resp = TextEmbedding.call(
            model=TextEmbedding.Models.text_embedding_v3,
            input=texts[:25],
            api_key=api_key,
        )
        if resp.status_code == 200 and resp.output and resp.output.get("embeddings"):
            return [emb.get("embedding", []) for emb in resp.output["embeddings"]]
        else:
            print(f"  [WARN] Embedding API 返回异常: {resp.status_code} {resp.message}")
    except Exception as e:
        print(f"  [WARN] Embedding API 调用异常: {e}")

    return []
