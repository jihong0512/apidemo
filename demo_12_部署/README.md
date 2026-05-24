# demo_12 生产部署 — 配置包

这不是 Python demo，而是第12讲"FastAPI工程化与生产部署"的参考配置。

## 文件说明

| 文件 | 用途 |
|:---|:---|
| `docker-compose.yml` | 六服务编排：MySQL + Neo4j + MinIO + Backend + Celery Worker |
| `../附录-环境搭建.md` | 完整环境搭建指南（init.sql、init.cypher、.env 示例、启动验证） |

## 快速启动（生产环境）

```bash
# 1. 修改 docker-compose.yml 中的密码
# 2. 启动所有服务
docker-compose up -d

# 3. 验证
curl http://localhost:8004/health
```

## 与 demo 的关系

- demo_02 ~ demo_10 是**纯 Python 本地运行**，不依赖 Docker
- 本配置是**生产部署参考**，给最终上线的环境使用
- demo_11 跑完之后，如果想在真实环境跑一遍，按本配置启动服务即可
