#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# API 测试平台 Demo — 一键环境安装脚本
# ═══════════════════════════════════════════════════════════════════
# 用法:
#   chmod +x setup.sh && ./setup.sh          # 完整安装
#   ./setup.sh --check                        # 仅检查状态
#   ./setup.sh --reinstall                    # 清理并重新安装
#
# 前置条件:
#   - Docker Desktop 已安装并运行
#   - Python 3.9+ 已安装
#
# 安装内容:
#   1. Docker: MySQL 8.0 + Neo4j 5.15
#   2. Python: pip install -r requirements.txt
#   3. 数据: MySQL 建表 + Neo4j 种子数据
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERR]${NC}   $*"; }
log_step()  { echo -e "\n${CYAN}━━━ $* ━━━${NC}"; }
log_ok()    { echo -e "${GREEN}  ✓${NC} $*"; }
log_fail()  { echo -e "${RED}  ✗${NC} $*"; }

# ═══════════════════════════════════════════════════════════════════
# 检查前置条件
# ═══════════════════════════════════════════════════════════════════
check_prerequisites() {
    log_step "Step 0: 检查前置条件"

    # Docker
    if ! command -v docker &>/dev/null; then
        log_error "Docker 未安装，请先安装 Docker Desktop"
        echo "  下载: https://www.docker.com/products/docker-desktop"
        exit 1
    fi
    log_ok "Docker: $(docker --version 2>/dev/null | head -1)"

    # Docker daemon 是否运行
    if ! docker info &>/dev/null; then
        log_error "Docker 守护进程未运行，请先启动 Docker Desktop"
        exit 1
    fi
    log_ok "Docker 守护进程: 运行中"

    # Docker Compose
    if docker compose version &>/dev/null; then
        log_ok "Docker Compose: $(docker compose version --short 2>/dev/null)"
    elif docker-compose --version &>/dev/null; then
        log_ok "Docker Compose (legacy): $(docker-compose --version 2>/dev/null)"
    else
        log_error "Docker Compose 不可用"
        exit 1
    fi

    # Python
    if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
        log_error "Python 未安装，请先安装 Python 3.9+"
        exit 1
    fi
    PYTHON=$(command -v python3 || command -v python)
    log_ok "Python: $($PYTHON --version 2>&1)"

    # pip
    if ! $PYTHON -m pip --version &>/dev/null; then
        log_error "pip 不可用"
        exit 1
    fi
    log_ok "pip: $($PYTHON -m pip --version 2>&1 | head -1)"
}

# ═══════════════════════════════════════════════════════════════════
# Docker 容器管理
# ═══════════════════════════════════════════════════════════════════
check_existing_containers() {
    log_step "Step 1: 检查现有容器"

    local containers=("api_test_mysql" "api_test_neo4j")
    local all_exist=true

    for container in "${containers[@]}"; do
        if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
            local status=$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null)
            case "$status" in
                running)
                    log_ok "$container: 运行中"
                    ;;
                exited|created)
                    log_warn "$container: 已停止, 正在启动..."
                    docker start "$container" >/dev/null 2>&1
                    log_ok "$container: 已启动"
                    ;;
                *)
                    log_warn "$container: 状态异常 ($status), 重新创建..."
                    docker rm -f "$container" >/dev/null 2>&1
                    all_exist=false
                    ;;
            esac
        else
            all_exist=false
        fi
    done

    if $all_exist; then
        log_info "所有容器已存在, 跳过 docker compose up"
        return 0
    else
        log_info "需要创建新容器..."
        start_containers
    fi
}

start_containers() {
    log_step "Step 2: 启动 Docker 容器"

    # 检查端口占用
    for port in 3309 7687; do
        if docker ps --format '{{.Ports}}' 2>/dev/null | grep -q "$port"; then
            log_warn "端口 $port 可能已被占用, 检查中..."
        fi
    done

    docker compose up -d 2>&1 | while IFS= read -r line; do
        echo "  $line"
    done

    if [ $? -ne 0 ]; then
        log_error "docker compose up 失败"
        exit 1
    fi
    log_ok "docker compose up 完成"
}

# ═══════════════════════════════════════════════════════════════════
# 等待健康检查
# ═══════════════════════════════════════════════════════════════════
wait_for_healthy() {
    log_step "Step 3: 等待服务健康检查"

    local services=(
        "api_test_mysql:MySQL:60"
        "api_test_neo4j:Neo4j:90"
    )

    for svc in "${services[@]}"; do
        IFS=':' read -r container name max_wait <<< "$svc"
        local waited=0
        echo -n "  等待 $name 就绪"

        while [ $waited -lt $max_wait ]; do
            local status=$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || echo "starting")

            if [ "$status" = "healthy" ]; then
                echo -e "\r  ${GREEN}✓${NC} $name: 就绪 (${waited}s)"
                break
            elif [ "$status" = "starting" ] || [ "$status" = "unhealthy" ]; then
                sleep 3
                waited=$((waited + 3))
                echo -n "."
            else
                # 容器没有 healthcheck, 直接检查状态
                local running=$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null)
                if [ "$running" = "true" ]; then
                    echo -e "\r  ${GREEN}✓${NC} $name: 运行中 (无健康检查)"
                    break
                fi
                sleep 2
                waited=$((waited + 2))
            fi
        done

        if [ $waited -ge $max_wait ]; then
            log_fail "$name: 超时 ($max_wait s)"
            log_warn "继续执行, 但部分功能可能不可用"
        fi
    done
}

# ═══════════════════════════════════════════════════════════════════
# Neo4j 数据初始化
# ═══════════════════════════════════════════════════════════════════
init_neo4j() {
    log_step "Step 4: Neo4j 种子数据"

    if [ ! -f "init-neo4j/init.cypher" ]; then
        log_warn "init-neo4j/init.cypher 不存在, 跳过"
        return
    fi

    # 检查 Neo4j 是否可连接
    local retries=0
    local max_retries=10

    while [ $retries -lt $max_retries ]; do
        if docker exec api_test_neo4j cypher-shell -u neo4j -p 123456789 "RETURN 1" >/dev/null 2>&1; then
            break
        fi
        sleep 3
        retries=$((retries + 1))
    done

    if [ $retries -ge $max_retries ]; then
        log_warn "Neo4j 未就绪, 跳过种子数据 (可稍后手动执行)"
        log_info "手动: docker exec -i api_test_neo4j cypher-shell -u neo4j -p 123456789 < init-neo4j/init.cypher"
        return
    fi

    echo "  执行 init-neo4j/init.cypher ..."
    if docker exec -i api_test_neo4j cypher-shell -u neo4j -p 123456789 < init-neo4j/init.cypher 2>&1 | tail -10; then
        log_ok "Neo4j 种子数据: 已写入"
    else
        # 可能是已存在数据(约束冲突), 不是致命错误
        log_warn "Neo4j 种子数据写入有警告 (可能已存在), 不影响使用"
    fi
}

# ═══════════════════════════════════════════════════════════════════
# Python 依赖安装
# ═══════════════════════════════════════════════════════════════════
install_python_deps() {
    log_step "Step 5: 安装 Python 依赖"

    local PYTHON=$(command -v python3 || command -v python)

    # 分层安装: 先基础, 再推荐, 最后可选 (可选失败不阻塞)
    log_info "安装基础依赖 (networkx, numpy)..."
    $PYTHON -m pip install --quiet networkx numpy 2>&1 | tail -1
    log_ok "基础依赖完成"

    log_info "安装推荐依赖 (faker, rank-bm25)..."
    $PYTHON -m pip install --quiet faker rank-bm25 2>&1 | tail -1 || log_warn "推荐依赖安装失败(非致命)"

    log_info "安装 LLM 客户端 (openai)..."
    $PYTHON -m pip install --quiet openai 2>&1 | tail -1 || log_warn "openai 安装失败(离线模式可用)"

    log_info "安装 Neo4j 驱动..."
    $PYTHON -m pip install --quiet neo4j 2>&1 | tail -1 || log_warn "neo4j 安装失败(降级到 networkx)"

    log_info "安装 ChromaDB..."
    $PYTHON -m pip install --quiet chromadb 2>&1 | tail -1 || log_warn "chromadb 安装失败(降级到 hash 向量)"

    log_info "安装 DashScope (Embedding + Reranker)..."
    $PYTHON -m pip install --quiet dashscope 2>&1 | tail -1 || log_warn "dashscope 安装失败(降级到 numpy)"

    log_info "安装 LangGraph (Agent 编排)..."
    $PYTHON -m pip install --quiet langgraph 2>&1 | tail -1 || log_warn "langgraph 安装失败(使用纯 Python 模拟)"

    log_info "安装 DeepDiff (Schema 变更检测)..."
    $PYTHON -m pip install --quiet deepdiff 2>&1 | tail -1 || log_warn "deepdiff 安装失败(降级到 JSON diff)"

    # 直接安装全部 (作为兜底)
    if [ -f "requirements.txt" ]; then
        log_info "安装 requirements.txt 全部依赖..."
        $PYTHON -m pip install --quiet -r requirements.txt 2>&1 | tail -1 || log_warn "部分可选依赖安装失败, 不影响降级模式运行"
    fi
}

# ═══════════════════════════════════════════════════════════════════
# MySQL 连接验证
# ═══════════════════════════════════════════════════════════════════
verify_mysql() {
    log_step "Step 6: 验证 MySQL 连接"

    if docker exec api_test_mysql mysql -u root -p123456 -e "USE api_test; SHOW TABLES;" 2>/dev/null; then
        log_ok "MySQL 连接成功, 表已创建"
    else
        log_warn "MySQL 验证失败, init.sql 可能未执行"
        log_info "手动: docker exec -i api_test_mysql mysql -u root -p123456 api_test < init-mysql/init.sql"
    fi
}

# ═══════════════════════════════════════════════════════════════════
# 打印最终状态
# ═══════════════════════════════════════════════════════════════════
print_status() {
    log_step "安装完成 — 环境状态"

    echo ""
    echo "  ┌─────────────────────────────────────────────────────┐"
    echo "  │            API 测试平台 Demo 环境                    │"
    echo "  ├─────────────────────────────────────────────────────┤"

    # 容器状态
    for container in api_test_mysql api_test_neo4j; do
        local status=$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo "未安装")
        local ports=$(docker inspect -f '{{range $p, $c := .NetworkSettings.Ports}}{{$p}} {{end}}' "$container" 2>/dev/null | tr '\n' ' ')
        if [ "$status" = "running" ]; then
            echo -e "  │  ${GREEN}✓${NC} ${container}: ${GREEN}${status}${NC} ${ports}"
        else
            echo -e "  │  ${RED}✗${NC} ${container}: ${RED}${status}${NC}"
        fi
    done

    # Python 包
    local PYTHON=$(command -v python3 || command -v python)
    echo "  ├─────────────────────────────────────────────────────┤"
    for pkg in networkx numpy faker openai neo4j chromadb langgraph; do
        if $PYTHON -c "import ${pkg//-/_}" 2>/dev/null; then
            echo -e "  │  ${GREEN}✓${NC} Python: ${pkg}"
        else
            echo -e "  │  ${YELLOW}○${NC} Python: ${pkg} (可选, 降级可用)"
        fi
    done

    echo "  ├─────────────────────────────────────────────────────┤"
    echo "  │  MySQL:    localhost:3309 (root/123456)"
    echo "  │  Neo4j:    bolt://localhost:7687 (neo4j/123456789)"
    echo "  │  Neo4j UI: http://localhost:7474"
    echo "  │  .env:     已配置 (DeepSeek + Qwen API Keys)"
    echo "  ├─────────────────────────────────────────────────────┤"
    echo "  │  运行流水线: cd demo_11_端到端 && python run_pipeline.py"
    echo "  └─────────────────────────────────────────────────────┘"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════
# 清理模式
# ═══════════════════════════════════════════════════════════════════
do_reinstall() {
    log_step "清理现有环境"
    docker compose down -v 2>/dev/null || true
    log_info "已清理, 开始全新安装..."
}

# ═══════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════

case "${1:-}" in
    --check)
        print_status
        exit 0
        ;;
    --reinstall)
        do_reinstall
        ;;
esac

echo ""
echo "  ╔═══════════════════════════════════════════════════════╗"
echo "  ║   API 测试平台 Demo — 一键环境安装                    ║"
echo "  ╚═══════════════════════════════════════════════════════╝"
echo ""

check_prerequisites
check_existing_containers
wait_for_healthy
init_neo4j
install_python_deps
verify_mysql
print_status

echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  一键安装完成! 现在可以运行 Demo 了${NC}"
echo -e "${GREEN}  cd demo_11_端到端 && python run_pipeline.py${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
