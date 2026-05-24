-- ═══════════════════════════════════════════════════════════════════
-- API 测试平台 Demo — MySQL 初始化脚本
-- ═══════════════════════════════════════════════════════════════════
-- Docker 首次启动时自动执行 (docker-entrypoint-initdb.d)
-- 如果数据库已存在则跳过 CREATE DATABASE (IF NOT EXISTS)
-- ═══════════════════════════════════════════════════════════════════

CREATE DATABASE IF NOT EXISTS api_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE api_test;

-- ═══════════════════════════════════════════════════════════════════
-- 1. 用户表
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 插入默认用户 (密码已 hash, 对应 123456)
INSERT IGNORE INTO users (id, username, email, password_hash) VALUES
(1, 'demo_user', 'demo@example.com', '$2b$12$LJ3m4ys3Gy4VX3j1FvQeAeGbzJhZ8Zz8Zz8Zz8Zz8Zz8Zz8Zz8Zz8');

-- ═══════════════════════════════════════════════════════════════════
-- 2. 项目表
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS projects (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    user_id INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO projects (id, name, description, user_id) VALUES
(1, 'Demo 设备管理系统', '教学演示项目 — 设备管理 CRUD 接口测试', 1);

-- ═══════════════════════════════════════════════════════════════════
-- 3. 文档表
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS documents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    filename VARCHAR(255) NOT NULL,
    file_type VARCHAR(50) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_size BIGINT,
    status VARCHAR(50) DEFAULT 'uploaded',
    parse_result LONGTEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════════════
-- 4. 接口信息表 (API 解析结果)
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS api_interfaces (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    name VARCHAR(200) NOT NULL,
    method VARCHAR(10) NOT NULL,
    url TEXT NOT NULL,
    description TEXT,
    headers TEXT,
    params TEXT,
    body TEXT,
    response_schema TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════════════
-- 5. 文档解析的详细接口表 (22 字段, 来自 enhanced_document_parser)
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS document_api_interfaces (
    id INT AUTO_INCREMENT PRIMARY KEY,
    document_id INT NOT NULL,
    project_id INT NOT NULL,
    name VARCHAR(200) NOT NULL COMMENT '接口名称',
    method VARCHAR(10) NOT NULL DEFAULT 'GET' COMMENT 'HTTP方法',
    url TEXT NOT NULL COMMENT '完整URL',
    base_url VARCHAR(500) COMMENT 'Base URL',
    path VARCHAR(500) COMMENT '请求路径',
    service VARCHAR(200) COMMENT '服务名',
    headers LONGTEXT COMMENT '请求头(JSON)',
    params LONGTEXT COMMENT 'URL参数(JSON)',
    request_body LONGTEXT COMMENT '请求体(JSON)',
    response_headers LONGTEXT COMMENT '响应头(JSON)',
    response_body LONGTEXT COMMENT '响应体(JSON)',
    response_schema LONGTEXT COMMENT '响应Schema(JSON)',
    status_code INT DEFAULT 200 COMMENT '响应状态码',
    description TEXT COMMENT '接口描述',
    tags TEXT COMMENT '标签(JSON数组)',
    deprecated TINYINT(1) DEFAULT 0 COMMENT '是否废弃',
    version VARCHAR(50) COMMENT '接口版本',
    xjid VARCHAR(50) COMMENT 'xjid字段',
    file_id VARCHAR(50) COMMENT '文件ID',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    INDEX idx_document_id (document_id),
    INDEX idx_project_id (project_id),
    INDEX idx_file_id (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════════════
-- 6. 测试用例表
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS test_cases (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    api_interface_id INT,
    name VARCHAR(200) NOT NULL,
    case_type VARCHAR(50) DEFAULT 'pytest',
    module VARCHAR(100) COMMENT '模块分类',
    description TEXT,
    test_data TEXT,
    test_code TEXT COMMENT '生成的测试代码',
    assertions TEXT,
    dependencies TEXT,
    status VARCHAR(50) DEFAULT 'active' COMMENT 'active/generating/completed/failed',
    generation_task_id VARCHAR(100) COMMENT 'Celery任务ID',
    generation_progress INT DEFAULT 0 COMMENT '生成进度0-100',
    error_message TEXT COMMENT '生成错误信息',
    generation_checkpoint LONGTEXT COMMENT '生成断点续传数据(JSON)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (api_interface_id) REFERENCES api_interfaces(id) ON DELETE SET NULL,
    INDEX idx_test_cases_module (module),
    INDEX idx_test_cases_status (status),
    INDEX idx_test_cases_project_status (project_id, status),
    INDEX idx_test_cases_project_module (project_id, module),
    INDEX idx_test_cases_name (project_id, name(50))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════════════
-- 7. 测试用例集合表
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS test_case_suites (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    test_case_ids TEXT COMMENT 'JSON格式用例ID列表',
    tags VARCHAR(500) COMMENT '标签逗号分隔',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    INDEX idx_test_case_suites_project_id (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════════════
-- 8. 测试环境表
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS test_environments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    name VARCHAR(100) NOT NULL COMMENT '环境名称',
    env_type VARCHAR(50) NOT NULL COMMENT '环境类型',
    base_url VARCHAR(500) NOT NULL COMMENT 'IP:port或域名',
    login_username VARCHAR(100) COMMENT '登录用户名',
    login_password VARCHAR(255) COMMENT '登录密码',
    xjid VARCHAR(50) DEFAULT '30110' COMMENT 'xjid字段',
    description TEXT,
    is_default TINYINT(1) DEFAULT 0 COMMENT '是否默认环境',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    INDEX idx_test_environments_project_id (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 插入默认测试环境
INSERT IGNORE INTO test_environments (id, project_id, name, env_type, base_url, login_username, login_password, description, is_default) VALUES
(1, 1, '本地测试环境', 'local', 'http://localhost:8000', 'admin', '123456', 'Demo 本地 Mock 环境', 1);

-- ═══════════════════════════════════════════════════════════════════
-- 9. 测试任务表
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS test_tasks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    name VARCHAR(200) NOT NULL,
    scenario TEXT COMMENT '执行场景描述',
    task_type VARCHAR(50) DEFAULT 'immediate' COMMENT 'immediate/scheduled',
    execution_task_type VARCHAR(50) DEFAULT 'interface' COMMENT 'scenario/interface/performance/other',
    cron_expression VARCHAR(100),
    test_case_ids TEXT COMMENT 'JSON格式用例ID列表(已排序)',
    test_case_suite_id INT COMMENT '用例集合ID',
    environment_id INT COMMENT '测试环境ID',
    threads INT DEFAULT 10 COMMENT '性能测试线程数',
    duration INT DEFAULT 5 COMMENT '性能测试执行时长(分钟)',
    dependency_analysis TEXT COMMENT '依赖关系分析结果(JSON)',
    test_data_config TEXT COMMENT '测试数据配置(JSON)',
    status VARCHAR(50) DEFAULT 'pending' COMMENT 'pending/running/paused/completed/failed/stopped',
    execution_task_id VARCHAR(100) COMMENT 'Celery执行任务ID',
    progress INT DEFAULT 0 COMMENT '执行进度0-100',
    execution_checkpoint LONGTEXT COMMENT '执行断点续传数据(JSON)',
    total_cases INT DEFAULT 0 COMMENT '总用例数',
    passed_cases INT DEFAULT 0 COMMENT '通过用例数',
    failed_cases INT DEFAULT 0 COMMENT '失败用例数',
    skipped_cases INT DEFAULT 0 COMMENT '跳过用例数',
    retry_count INT DEFAULT 0 COMMENT '重试次数',
    max_retries INT DEFAULT 3 COMMENT '最大重试次数',
    executed_at TIMESTAMP NULL,
    completed_at TIMESTAMP NULL,
    paused_at TIMESTAMP NULL COMMENT '暂停时间',
    error_message TEXT COMMENT '错误信息',
    result_summary TEXT COMMENT '结果摘要',
    allure_report_path VARCHAR(500) COMMENT 'Allure报告路径',
    jtl_report_path VARCHAR(500) COMMENT 'JTL报告路径(HTML)',
    performance_analysis LONGTEXT COMMENT '性能分析结果',
    performance_report_html LONGTEXT COMMENT '性能分析报告HTML',
    execution_logs LONGTEXT COMMENT '执行日志',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (test_case_suite_id) REFERENCES test_case_suites(id) ON DELETE SET NULL,
    FOREIGN KEY (environment_id) REFERENCES test_environments(id) ON DELETE SET NULL,
    INDEX idx_test_tasks_project_id (project_id),
    INDEX idx_test_tasks_status (status),
    INDEX idx_test_tasks_environment_id (environment_id),
    INDEX idx_test_tasks_project_status (project_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════════════
-- 10. 测试结果表
-- ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS test_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_id INT NOT NULL,
    test_case_id INT NOT NULL,
    status VARCHAR(50) NOT NULL COMMENT 'passed/failed/skipped/error',
    request_data TEXT COMMENT '请求数据(JSON)',
    response_data TEXT COMMENT '响应数据(JSON)',
    assertions_result TEXT COMMENT '断言结果(JSON)',
    error_message TEXT COMMENT '错误信息',
    execution_time DECIMAL(10, 3) COMMENT '执行耗时(秒)',
    request_size INT COMMENT '请求大小(字节)',
    response_size INT COMMENT '响应大小(字节)',
    status_code INT COMMENT 'HTTP状态码',
    performance_metrics TEXT COMMENT '性能指标(JSON)',
    failure_analysis TEXT COMMENT '失败分析结果(JSON)',
    ai_suggestions TEXT COMMENT 'AI优化建议(JSON)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES test_tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    INDEX idx_test_results_task_id (task_id),
    INDEX idx_test_results_status (status),
    INDEX idx_test_results_created_at (created_at),
    INDEX idx_test_results_task_status (task_id, status),
    INDEX idx_test_results_case_status (test_case_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════════════
-- 11. 补充表: 调试记录 / 数据库连接 / 向量文档 / 元数据 / 快照 / 变更历史 / 更新建议
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS test_debug_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    test_case_id INT NOT NULL,
    environment_id INT,
    task_id VARCHAR(100) COMMENT 'Celery任务ID',
    execution_status VARCHAR(50) DEFAULT 'pending',
    execution_result TEXT COMMENT '执行结果摘要',
    debug_logs LONGTEXT COMMENT '调试日志(完整输出)',
    error_message TEXT COMMENT '错误信息',
    execution_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duration INT COMMENT '执行耗时(秒)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_test_case_id (test_case_id),
    INDEX idx_environment_id (environment_id),
    INDEX idx_task_id (task_id),
    FOREIGN KEY (test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    FOREIGN KEY (environment_id) REFERENCES test_environments(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='测试用例调试记录表';

CREATE TABLE IF NOT EXISTS db_connections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    db_type VARCHAR(50) NOT NULL,
    host VARCHAR(200) NOT NULL,
    port INT NOT NULL,
    database_name VARCHAR(100) NOT NULL,
    username VARCHAR(100) NOT NULL,
    password VARCHAR(255) NOT NULL,
    status VARCHAR(50) DEFAULT 'inactive',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS vector_documents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    document_id INT NOT NULL,
    chunk_text TEXT NOT NULL,
    chunk_index INT NOT NULL,
    vector_id VARCHAR(100),
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS table_metadata (
    id INT AUTO_INCREMENT PRIMARY KEY,
    db_connection_id INT NOT NULL,
    table_name VARCHAR(200) NOT NULL,
    table_comment TEXT COMMENT '表的含义/注释',
    primary_keys TEXT COMMENT '主键列表(JSON)',
    indexes TEXT COMMENT '索引信息(JSON)',
    foreign_keys TEXT COMMENT '外键信息(JSON)',
    column_count INT DEFAULT 0,
    row_count BIGINT DEFAULT 0,
    metadata TEXT COMMENT '额外元数据(JSON)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (db_connection_id) REFERENCES db_connections(id) ON DELETE CASCADE,
    UNIQUE KEY uk_table (db_connection_id, table_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS column_metadata (
    id INT AUTO_INCREMENT PRIMARY KEY,
    table_metadata_id INT NOT NULL,
    column_name VARCHAR(200) NOT NULL,
    column_comment TEXT COMMENT '字段含义/注释',
    data_type VARCHAR(100) NOT NULL,
    is_nullable VARCHAR(10) DEFAULT 'YES',
    default_value TEXT,
    is_primary_key TINYINT(1) DEFAULT 0,
    is_foreign_key TINYINT(1) DEFAULT 0,
    auto_increment TINYINT(1) DEFAULT 0,
    position INT COMMENT '字段位置',
    metadata TEXT COMMENT '额外元数据(JSON)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (table_metadata_id) REFERENCES table_metadata(id) ON DELETE CASCADE,
    UNIQUE KEY uk_column (table_metadata_id, column_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS table_relationships (
    id INT AUTO_INCREMENT PRIMARY KEY,
    db_connection_id INT NOT NULL,
    source_table_id INT NOT NULL,
    target_table_id INT NOT NULL,
    relationship_type VARCHAR(50) NOT NULL COMMENT '关系类型: has_a/is_a/depend_on/foreign_key',
    relationship_name VARCHAR(200) COMMENT '关系名称',
    foreign_key_columns TEXT COMMENT '外键字段(JSON)',
    referred_columns TEXT COMMENT '引用字段(JSON)',
    description TEXT COMMENT '关系描述',
    cypher_query TEXT COMMENT 'Cypher查询语句',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (db_connection_id) REFERENCES db_connections(id) ON DELETE CASCADE,
    FOREIGN KEY (source_table_id) REFERENCES table_metadata(id) ON DELETE CASCADE,
    FOREIGN KEY (target_table_id) REFERENCES table_metadata(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS api_document_snapshots (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    document_id INT NOT NULL,
    snapshot_data TEXT COMMENT '快照数据(JSON格式存储接口列表)',
    version VARCHAR(50) COMMENT '版本号',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS api_change_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    snapshot_id INT NOT NULL,
    old_snapshot_id INT COMMENT '旧快照ID',
    change_type VARCHAR(50) NOT NULL COMMENT '变更类型: added/deleted/modified',
    change_summary TEXT COMMENT '变更摘要(JSON)',
    affected_interfaces TEXT COMMENT '受影响接口ID列表(JSON)',
    change_level VARCHAR(20) COMMENT '变更级别: low/medium/high/breaking',
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES api_document_snapshots(id) ON DELETE CASCADE,
    FOREIGN KEY (old_snapshot_id) REFERENCES api_document_snapshots(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS update_suggestions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    change_history_id INT NOT NULL,
    test_case_id INT NOT NULL,
    strategy VARCHAR(50) NOT NULL COMMENT '更新策略: regenerate/incremental',
    reasoning TEXT COMMENT '策略选择理由',
    update_plan TEXT COMMENT '更新计划(JSON)',
    manual_interventions TEXT COMMENT '需要人工介入部分(JSON)',
    estimated_effort VARCHAR(20) COMMENT '预估工作量: low/medium/high',
    automation_rate DECIMAL(5, 2) COMMENT '自动化率 0-1',
    status VARCHAR(50) DEFAULT 'pending' COMMENT 'pending/applied/rejected/ignored',
    applied_at TIMESTAMP COMMENT '应用时间',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (change_history_id) REFERENCES api_change_history(id) ON DELETE CASCADE,
    FOREIGN KEY (test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════════════
-- 验证: 列出所有创建的表
-- ═══════════════════════════════════════════════════════════════════
SELECT CONCAT('[OK] ', TABLE_NAME, ' (', TABLE_ROWS, ' rows)') AS init_status
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'api_test'
ORDER BY TABLE_NAME;
