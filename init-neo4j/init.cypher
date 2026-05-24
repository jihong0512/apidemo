// ═══════════════════════════════════════════════════════════════════
// API 测试平台 Demo — Neo4j 初始化 Cypher 脚本
// ═══════════════════════════════════════════════════════════════════
// 用途: 为 demo_03 知识图谱构建提供种子数据
// 执行: docker exec -i api_test_neo4j cypher-shell -u neo4j -p 123456789 < init-neo4j/init.cypher
// 或在容器内: cypher-shell -u neo4j -p 123456789 -f /init-neo4j/init.cypher
// ═══════════════════════════════════════════════════════════════════

// ── 清理旧数据 (幂等: 首次运行无影响) ──
MATCH (n) DETACH DELETE n;

// ── 约束: 确保接口名称唯一 ──
CREATE CONSTRAINT unique_api_name IF NOT EXISTS
FOR (a:APIInterface) REQUIRE a.name IS UNIQUE;

CREATE CONSTRAINT unique_group_name IF NOT EXISTS
FOR (g:BusinessGroup) REQUIRE g.name IS UNIQUE;

// ═══════════════════════════════════════════════════════════════════
// 1. 创建 APIInterface 节点 (6个接口 — 来自 sample_swagger.json)
// ═══════════════════════════════════════════════════════════════════

CREATE (login:APIInterface {
    name: '用户手机号登录',
    method: 'POST',
    url: '/api/v1/login/phone',
    service: 'auth',
    module: '认证',
    description: '手机号+验证码登录，返回JWT Token',
    tags: ['auth', 'login'],
    version: 'v1'
});

CREATE (createDevice:APIInterface {
    name: '创建设备',
    method: 'POST',
    url: '/api/v1/device/create',
    service: 'device',
    module: '设备管理',
    description: '创建新的智能设备记录，需要管理员权限',
    tags: ['device', 'create'],
    version: 'v1'
});

CREATE (listDevices:APIInterface {
    name: '查询设备列表',
    method: 'GET',
    url: '/api/v1/device/list',
    service: 'device',
    module: '设备管理',
    description: '分页查询设备列表，支持按类型/状态筛选',
    tags: ['device', 'query', 'list'],
    version: 'v1'
});

CREATE (getDevice:APIInterface {
    name: '查询设备详情',
    method: 'GET',
    url: '/api/v1/device/{device_id}',
    service: 'device',
    module: '设备管理',
    description: '根据设备ID查询设备详细信息',
    tags: ['device', 'query', 'detail'],
    version: 'v1'
});

CREATE (updateDevice:APIInterface {
    name: '更新设备信息',
    method: 'PUT',
    url: '/api/v1/device/{device_id}',
    service: 'device',
    module: '设备管理',
    description: '更新设备名称/描述/状态等字段',
    tags: ['device', 'update'],
    version: 'v1'
});

CREATE (deleteDevice:APIInterface {
    name: '删除设备',
    method: 'DELETE',
    url: '/api/v1/device/{device_id}',
    service: 'device',
    module: '设备管理',
    description: '软删除设备记录，需要超级管理员权限',
    tags: ['device', 'delete'],
    version: 'v1'
});

// ═══════════════════════════════════════════════════════════════════
// 2. 创建 BusinessGroup 节点 (4组 — 来自 demo_04 分组算法)
// ═══════════════════════════════════════════════════════════════════

CREATE (authGroup:BusinessGroup {name: '认证服务', type: 'service', description: '用户认证与授权'});
CREATE (deviceGroup:BusinessGroup {name: '设备管理', type: 'module', description: '设备 CRUD 操作'});
CREATE (queryGroup:BusinessGroup {name: '查询服务', type: 'url_pattern', description: 'GET 查询类接口'});
CREATE (mutationGroup:BusinessGroup {name: '变更服务', type: 'url_pattern', description: 'POST/PUT/DELETE 变更类接口'});

// ═══════════════════════════════════════════════════════════════════
// 3. 创建业务关系 (15种关系类型)
// ═══════════════════════════════════════════════════════════════════

// ── 3.1 包含关系: 组 → 接口 ──
CREATE (authGroup)-[:CONTAINS {weight: 1.0}]->(login);
CREATE (deviceGroup)-[:CONTAINS {weight: 1.0}]->(createDevice);
CREATE (deviceGroup)-[:CONTAINS {weight: 1.0}]->(listDevices);
CREATE (deviceGroup)-[:CONTAINS {weight: 1.0}]->(getDevice);
CREATE (deviceGroup)-[:CONTAINS {weight: 1.0}]->(updateDevice);
CREATE (deviceGroup)-[:CONTAINS {weight: 1.0}]->(deleteDevice);
CREATE (queryGroup)-[:CONTAINS {weight: 0.8}]->(listDevices);
CREATE (queryGroup)-[:CONTAINS {weight: 0.8}]->(getDevice);
CREATE (mutationGroup)-[:CONTAINS {weight: 0.8}]->(createDevice);
CREATE (mutationGroup)-[:CONTAINS {weight: 0.8}]->(updateDevice);
CREATE (mutationGroup)-[:CONTAINS {weight: 0.8}]->(deleteDevice);

// ── 3.2 依赖关系: 接口间的调用顺序 ──
//   CRUD 依赖链: login → create → list → get → update → delete
CREATE (createDevice)-[:DEPENDS_ON {type: 'auth', description: '需要登录Token'}]->(login);
CREATE (listDevices)-[:DEPENDS_ON {type: 'auth', description: '需要登录Token'}]->(login);
CREATE (getDevice)-[:DEPENDS_ON {type: 'auth', description: '需要登录Token'}]->(login);
CREATE (updateDevice)-[:DEPENDS_ON {type: 'auth', description: '需要登录Token'}]->(login);
CREATE (deleteDevice)-[:DEPENDS_ON {type: 'auth', description: '需要登录Token'}]->(login);

// ── 3.3 数据流依赖: 前置接口的输出是后置接口的输入 ──
CREATE (listDevices)-[:DATA_FLOW {field: 'device_id', description: '列表返回的device_id用于查询详情'}]->(createDevice);
CREATE (getDevice)-[:DATA_FLOW {field: 'device_id', description: '创建的device_id用于查询详情验证'}]->(createDevice);
CREATE (updateDevice)-[:DATA_FLOW {field: 'device_id', description: '需要先获取device_id再更新'}]->(getDevice);
CREATE (deleteDevice)-[:DATA_FLOW {field: 'device_id', description: '需要先获取device_id再删除'}]->(getDevice);

// ── 3.4 Token 流: 登录返回的 token 注入到后续请求 ──
CREATE (login)-[:PROVIDES_TOKEN {field: 'token', ttl: 3600}]->(createDevice);
CREATE (login)-[:PROVIDES_TOKEN {field: 'token', ttl: 3600}]->(listDevices);
CREATE (login)-[:PROVIDES_TOKEN {field: 'token', ttl: 3600}]->(getDevice);
CREATE (login)-[:PROVIDES_TOKEN {field: 'token', ttl: 3600}]->(updateDevice);
CREATE (login)-[:PROVIDES_TOKEN {field: 'token', ttl: 3600}]->(deleteDevice);

// ── 3.5 CRUD 链: CREATE → READ → UPDATE → DELETE (标准 REST 拓扑) ──
CREATE (createDevice)-[:PRECEDES {order: 1, reason: '先创建资源'}]->(getDevice);
CREATE (getDevice)-[:PRECEDES {order: 2, reason: '确认资源存在'}]->(updateDevice);
CREATE (updateDevice)-[:PRECEDES {order: 3, reason: '确认更新成功'}]->(deleteDevice);

// ── 3.6 互斥关系 ──
CREATE (deleteDevice)-[:MUTUALLY_EXCLUSIVE_WITH {reason: '不能同时创建和删除同一个资源'}]->(createDevice);

// ═══════════════════════════════════════════════════════════════════
// 4. 关联关系: 组之间的关联
// ═══════════════════════════════════════════════════════════════════
CREATE (deviceGroup)-[:BELONGS_TO {weight: 0.9}]->(mutationGroup);
CREATE (deviceGroup)-[:BELONGS_TO {weight: 0.7}]->(queryGroup);
CREATE (authGroup)-[:ASSOCIATES_WITH {weight: 0.5}]->(deviceGroup);

// ═══════════════════════════════════════════════════════════════════
// 5. 验证节点统计
// ═══════════════════════════════════════════════════════════════════
MATCH (n) RETURN '节点总数' AS stat, count(n) AS count
UNION ALL
MATCH ()-[r]->() RETURN '关系总数' AS stat, count(r) AS count
UNION ALL
MATCH (a:APIInterface) RETURN 'APIInterface节点' AS stat, count(a) AS count
UNION ALL
MATCH (g:BusinessGroup) RETURN 'BusinessGroup节点' AS stat, count(g) AS count;
