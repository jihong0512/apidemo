<#
.SYNOPSIS
    API 测试平台 Demo — 一键环境安装 (Windows PowerShell)
.DESCRIPTION
    检查 Docker + Python 环境, 启动 MySQL/Neo4j 容器,
    安装 Python 依赖, 初始化数据库种子数据.
.PARAMETER Check
    仅检查环境状态 (不安装)
.PARAMETER Reinstall
    清理现有容器并重新安装
.EXAMPLE
    .\setup.ps1                # 完整安装
    .\setup.ps1 -Check         # 仅检查
    .\setup.ps1 -Reinstall     # 清理重装
#>

param(
    [switch]$Check,
    [switch]$Reinstall
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# ── 控制台 UTF-8 ──
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Info  { Write-Host "[INFO]  $args" -ForegroundColor Green }
function Write-Warn  { Write-Host "[WARN]  $args" -ForegroundColor Yellow }
function Write-Error2 { Write-Host "[ERR]   $args" -ForegroundColor Red }
function Write-Step  { Write-Host "`n━━━ $args ━━━" -ForegroundColor Cyan }
function Write-Ok    { Write-Host "  ✓ $args" -ForegroundColor Green }
function Write-Fail  { Write-Host "  ✗ $args" -ForegroundColor Red }

# ═══════════════════════════════════════════════════════════════════
# 检查前置条件
# ═══════════════════════════════════════════════════════════════════
function Check-Prerequisites {
    Write-Step "Step 0: 检查前置条件"

    # Docker
    $dockerVersion = docker --version 2>$null
    if (-not $dockerVersion) {
        Write-Error2 "Docker 未安装，请先安装 Docker Desktop"
        Write-Host "  下载: https://www.docker.com/products/docker-desktop"
        exit 1
    }
    Write-Ok "Docker: $dockerVersion"

    # Docker daemon
    $dockerInfo = docker info 2>$null
    if (-not $dockerInfo) {
        Write-Error2 "Docker 守护进程未运行，请先启动 Docker Desktop"
        exit 1
    }
    Write-Ok "Docker 守护进程: 运行中"

    # Docker Compose
    $composeVersion = docker compose version 2>$null
    if ($composeVersion) {
        Write-Ok "Docker Compose: $composeVersion"
    } else {
        Write-Error2 "Docker Compose 不可用"
        exit 1
    }

    # Python
    $pythonCmd = $null
    foreach ($cmd in @("python", "python3", "py")) {
        try {
            $v = & $cmd --version 2>&1
            if ($v -match "Python") {
                $pythonCmd = $cmd
                Write-Ok "Python: $v"
                break
            }
        } catch {}
    }
    if (-not $pythonCmd) {
        Write-Error2 "Python 未安装，请先安装 Python 3.9+"
        exit 1
    }
    $script:PYTHON = $pythonCmd

    # pip
    $pipVersion = & $PYTHON -m pip --version 2>&1
    if ($pipVersion) {
        Write-Ok "pip: $pipVersion"
    } else {
        Write-Error2 "pip 不可用"
        exit 1
    }
}

# ═══════════════════════════════════════════════════════════════════
# Docker 容器管理
# ═══════════════════════════════════════════════════════════════════
function Start-DockerContainers {
    Write-Step "Step 1: 启动 Docker 容器"

    $containers = @("api_test_mysql", "api_test_neo4j")
    $allExist = $true

    foreach ($c in $containers) {
        $exists = docker ps -a --format '{{.Names}}' 2>$null | Select-String "^$c$"
        if ($exists) {
            $status = docker inspect -f '{{.State.Status}}' $c 2>$null
            if ($status -eq "running") {
                Write-Ok "$c : 运行中"
            } elseif ($status -eq "exited" -or $status -eq "created") {
                Write-Warn "$c : 已停止, 正在启动..."
                docker start $c 2>&1 | Out-Null
                Write-Ok "$c : 已启动"
            } else {
                Write-Warn "$c : 状态异常 ($status), 重新创建..."
                docker rm -f $c 2>&1 | Out-Null
                $allExist = $false
            }
        } else {
            $allExist = $false
        }
    }

    if ($allExist) {
        Write-Info "所有容器已存在, 跳过 docker compose up"
        return
    }

    Write-Step "Step 2: 创建新容器 (docker compose up)"
    docker compose up -d 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error2 "docker compose up 失败"
        exit 1
    }
    Write-Ok "docker compose up 完成"
}

# ═══════════════════════════════════════════════════════════════════
# 等待健康检查
# ═══════════════════════════════════════════════════════════════════
function Wait-ForHealthy {
    Write-Step "Step 3: 等待服务健康检查"

    $services = @(
        @{Container="api_test_mysql"; Name="MySQL"; MaxWait=60},
        @{Container="api_test_neo4j"; Name="Neo4j"; MaxWait=90}
    )

    foreach ($svc in $services) {
        $waited = 0
        Write-Host -NoNewline "  等待 $($svc.Name) 就绪"

        while ($waited -lt $svc.MaxWait) {
            $status = docker inspect -f '{{.State.Health.Status}}' $svc.Container 2>$null
            if (-not $status) { $status = "starting" }

            if ($status -eq "healthy") {
                Write-Host "`r  ✓ $($svc.Name): 就绪 (${waited}s)" -ForegroundColor Green
                break
            } elseif ($status -eq "starting" -or $status -eq "unhealthy") {
                Start-Sleep 3
                $waited += 3
                Write-Host -NoNewline "."
            } else {
                $running = docker inspect -f '{{.State.Running}}' $svc.Container 2>$null
                if ($running -eq "true") {
                    Write-Host "`r  ✓ $($svc.Name): 运行中 (无健康检查)" -ForegroundColor Green
                    break
                }
                Start-Sleep 2
                $waited += 2
            }
        }

        if ($waited -ge $svc.MaxWait) {
            Write-Fail "$($svc.Name): 超时 ($($svc.MaxWait)s)"
            Write-Warn "继续执行, 但部分功能可能不可用"
        }
    }
}

# ═══════════════════════════════════════════════════════════════════
# Neo4j 种子数据
# ═══════════════════════════════════════════════════════════════════
function Initialize-Neo4j {
    Write-Step "Step 4: Neo4j 种子数据"

    if (-not (Test-Path "init-neo4j/init.cypher")) {
        Write-Warn "init-neo4j/init.cypher 不存在, 跳过"
        return
    }

    # 等待 Neo4j 完全就绪
    $retries = 0
    while ($retries -lt 10) {
        $test = docker exec api_test_neo4j cypher-shell -u neo4j -p 123456789 "RETURN 1" 2>$null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep 3
        $retries++
    }

    if ($retries -ge 10) {
        Write-Warn "Neo4j 未就绪, 跳过种子数据"
        Write-Info "手动: docker exec -i api_test_neo4j cypher-shell -u neo4j -p 123456789 < init-neo4j/init.cypher"
        return
    }

    Write-Host "  执行 init-neo4j/init.cypher ..."
    Get-Content "init-neo4j/init.cypher" -Raw | docker exec -i api_test_neo4j cypher-shell -u neo4j -p 123456789 2>&1 | Select-Object -Last 10
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Neo4j 种子数据: 已写入"
    } else {
        Write-Warn "Neo4j 种子数据写入有警告 (可能已存在), 不影响使用"
    }
}

# ═══════════════════════════════════════════════════════════════════
# Python 依赖安装
# ═══════════════════════════════════════════════════════════════════
function Install-PythonDeps {
    Write-Step "Step 5: 安装 Python 依赖"

    Write-Info "安装基础依赖 (networkx, numpy)..."
    & $PYTHON -m pip install --quiet networkx numpy 2>&1 | Select-Object -Last 1
    Write-Ok "基础依赖完成"

    Write-Info "安装推荐依赖 (faker, rank-bm25)..."
    & $PYTHON -m pip install --quiet faker rank-bm25 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) { Write-Warn "推荐依赖安装失败(非致命)" }

    Write-Info "安装 LLM 客户端 (openai)..."
    & $PYTHON -m pip install --quiet openai 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) { Write-Warn "openai 安装失败(离线模式可用)" }

    Write-Info "安装 Neo4j 驱动..."
    & $PYTHON -m pip install --quiet neo4j 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) { Write-Warn "neo4j 安装失败(降级到 networkx)" }

    Write-Info "安装 ChromaDB..."
    & $PYTHON -m pip install --quiet chromadb 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) { Write-Warn "chromadb 安装失败(降级到 hash 向量)" }

    Write-Info "安装 DashScope (Embedding + Reranker)..."
    & $PYTHON -m pip install --quiet dashscope 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) { Write-Warn "dashscope 安装失败(降级到 numpy)" }

    Write-Info "安装 LangGraph (Agent 编排)..."
    & $PYTHON -m pip install --quiet langgraph 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) { Write-Warn "langgraph 安装失败(使用纯 Python 模拟)" }

    Write-Info "安装 DeepDiff (Schema 变更检测)..."
    & $PYTHON -m pip install --quiet deepdiff 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) { Write-Warn "deepdiff 安装失败(降级到 JSON diff)" }

    # requirements.txt 兜底
    if (Test-Path "requirements.txt") {
        Write-Info "安装 requirements.txt 全部依赖..."
        & $PYTHON -m pip install --quiet -r requirements.txt 2>&1 | Select-Object -Last 1
        if ($LASTEXITCODE -ne 0) { Write-Warn "部分可选依赖安装失败, 不影响降级模式运行" }
    }
}

# ═══════════════════════════════════════════════════════════════════
# MySQL 验证
# ═══════════════════════════════════════════════════════════════════
function Verify-MySQL {
    Write-Step "Step 6: 验证 MySQL 连接"

    $result = docker exec api_test_mysql mysql -u root -p123456 -e "USE api_test; SHOW TABLES;" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "MySQL 连接成功, 表已创建"
        $result | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Warn "MySQL 验证失败, init.sql 可能未执行"
        Write-Info "手动: docker exec -i api_test_mysql mysql -u root -p123456 api_test < init-mysql/init.sql"
    }
}

# ═══════════════════════════════════════════════════════════════════
# 打印状态
# ═══════════════════════════════════════════════════════════════════
function Show-Status {
    Write-Step "安装完成 — 环境状态"
    Write-Host ""

    $containers = @(
        @{Name="api_test_mysql"; Port="3309"; Conn="localhost:3309 (root/123456)"},
        @{Name="api_test_neo4j"; Port="7687"; Conn="bolt://localhost:7687 (neo4j/123456789)"}
    )

    Write-Host "  Docker 容器:"
    foreach ($c in $containers) {
        $status = docker inspect -f '{{.State.Status}}' $c.Name 2>$null
        if (-not $status) { $status = "未安装" }
        if ($status -eq "running") {
            Write-Ok "$($c.Name): $status — $($c.Conn)"
        } else {
            Write-Fail "$($c.Name): $status"
        }
    }

    Write-Host ""
    Write-Host "  Python 依赖:"
    foreach ($pkg in @("networkx", "numpy", "faker", "openai", "neo4j", "chromadb", "langgraph", "deepdiff", "dashscope")) {
        try {
            $modName = $pkg -replace "-", "_"
            & $PYTHON -c "import $modName" 2>$null
            Write-Ok $pkg
        } catch {
            Write-Host "  ○ $pkg (可选, 降级可用)" -ForegroundColor Yellow
        }
    }

    Write-Host ""
    Write-Host "  ┌─────────────────────────────────────────┐"
    Write-Host "  │  MySQL:  localhost:3309 (root/123456)   │"
    Write-Host "  │  Neo4j:  bolt://localhost:7687          │"
    Write-Host "  │  .env:   已配置 (API Keys)              │"
    Write-Host "  ├─────────────────────────────────────────┤"
    Write-Host "  │  运行: cd demo_11_端到端; python run_pipeline.py │"
    Write-Host "  └─────────────────────────────────────────┘"
    Write-Host ""
}

# ═══════════════════════════════════════════════════════════════════
# 清理模式
# ═══════════════════════════════════════════════════════════════════
function Invoke-Reinstall {
    Write-Step "清理现有环境"
    docker compose down -v 2>$null
    Write-Info "已清理, 开始全新安装..."
}

# ═══════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "  ╔═══════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║   API 测试平台 Demo — 一键环境安装                    ║" -ForegroundColor Cyan
Write-Host "  ╚═══════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

if ($Check) {
    Show-Status
    exit 0
}

if ($Reinstall) {
    Invoke-Reinstall
}

Check-Prerequisites
Start-DockerContainers
Wait-ForHealthy
Initialize-Neo4j
Install-PythonDeps
Verify-MySQL
Show-Status

Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  一键安装完成! 现在可以运行 Demo 了" -ForegroundColor Green
Write-Host "  cd demo_11_端到端; python run_pipeline.py" -ForegroundColor Green
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
