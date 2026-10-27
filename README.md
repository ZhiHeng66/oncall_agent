# 企业知识服务多智能体协同平台

> 基于 LangChain 组件与 LangGraph 编排、Supervisor-Specialist、Milvus RAG 和只读 MCP 工具的多智能体应用。

[![Python](https://img.shields.io/badge/Python-3.11--3.13-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-latest-orange.svg)](https://www.langchain.com/)

## ✨ 核心特性

- **Supervisor 自动路由**：支持 direct、single、parallel 和 complex 四种路径。
- **Knowledge Agent**：Milvus 检索、标准化证据和来源引用。
- **Data Agent**：通过严格白名单调用只读本地/MCP 数据工具。
- **Task Agent**：使用有限 Plan-Execute-Replan 子图执行有依赖的复杂任务。
- **Reviewer Agent**：检查证据、引用和错误披露，最多定向返工一次。
- **统一 API 与 SSE**：提供 JSON 响应、Agent 时间线、来源和明确终态。
- **PostgreSQL 持久化与工作记忆**：默认启动 PostgreSQL，保存顶层 Checkpoint、运行记录、安全 Trace 和按 `thread_id` 的案件工作记忆。
- **固定离线 Eval**：34 条用例覆盖路由、检索引用契约、工具计划校验、任务终态和 Reviewer；`make eval` 可重复生成同数据集比较报告。
- **业务演示数据**：产品手册、业务规则、FAQ、交付指南，以及只读工单 MCP 数据源。
- **兼容接口**：迁移期保留 `/api/chat`、`/api/chat_stream` 和 `/api/aiops`。

## 🛠️ 技术栈

- **框架**: FastAPI + LangChain + LangGraph
- **LLM**: 阿里云 DashScope (通义千问)
- **向量库**: Milvus
- **持久化**: PostgreSQL（默认）/ SQLite、memory（显式本地回退）
- **工具协议**: MCP (Model Context Protocol)

## 🚀 快速开始

### 环境要求
- Python 3.11 至 3.13
- 阿里云 DashScope API Key ([获取地址](https://dashscope.aliyun.com/))

### 安装和启动

#### Linux/macOS 环境

```bash
# 1. 克隆项目
git clone <repository_url>
cd Oncall_agent

# 2. 安装依赖（推荐使用 uv）
# 方式 1: 使用 uv（推荐，更快）
pip install uv
uv venv
source .venv/bin/activate
uv pip install -e .

# 方式 2: 使用 pip
pip install -e .

# 3. 创建并编辑配置文件：填入 DASHSCOPE_API_KEY，并为 PostgreSQL 设置强密码
cp .env.example .env
vim .env  # 或使用其他编辑器

# 4. 一键初始化（启动 Milvus + PostgreSQL、初始化 Schema、启动服务、上传文档）
make init

# 5. 一键启动
make start
```

#### Windows 环境（PowerShell/CMD）

如果Windows 不支持 `make` 命令，可以手动执行以下步骤以启动服务：

```powershell
# 1. 克隆项目
git clone <repository_url>
cd Oncall_agent

# 2. 创建虚拟环境并安装依赖
# 方式 1: 使用 uv（推荐，更快）
pip install uv
# 创建虚拟环境
uv venv
# 激活虚拟环境
.venv\Scripts\activate
# 安装所有依赖
uv pip install -e .

# 方式 2: 使用 pip
python -m venv .venv
.venv\Scripts\activate
pip install -e .

# 3. 编辑配置文件
# 从示例创建配置，再填入 DASHSCOPE_API_KEY 和 POSTGRES_PASSWORD
copy .env.example .env
notepad .env

# 4. 启动 Docker Desktop
# 确保 Docker Desktop 已安装并正在运行

# 5. 启动 Milvus 向量数据库（Docker Compose）
docker compose -f vector-database.yml up -d

# 6. 启动 PostgreSQL 并初始化 Schema
docker compose -f postgres-database.yml up -d
python -m app.cli.database upgrade

# 7. 等待 Milvus 启动完成（约 5-10 秒）
timeout /t 10

# 8. 启动 MCP 服务
# 启动 CLS 日志查询服务（新开一个 PowerShell 窗口）
python mcp_servers/cls_server.py

# 启动 Monitor 监控服务（新开一个 PowerShell 窗口）
python mcp_servers/monitor_server.py

# 启动只读业务工单服务
python mcp_servers/ticket_server.py

# 9. 启动 FastAPI 主服务（新开一个 PowerShell 窗口）
# 注意：日志会自动输出到 logs\app_YYYY-MM-DD.log
python -m uvicorn app.main:app --host 0.0.0.0 --port 9900

# 10. 上传文档到向量库（新开一个 PowerShell 窗口）
# 等待服务启动完成后执行
timeout /t 5
# 可逐个将 business-docs/ 下的 Markdown 上传至 /api/upload
```

**Windows 一键启动脚本**（推荐）

使用启动脚本：

```powershell
# 启动所有服务
.\start-windows.bat

# 停止所有服务
.\stop-windows.bat
```

### 访问服务
- **Web 界面**: http://localhost:9900
- **API 文档**: http://localhost:9900/docs

## 📡 API 接口

### 核心接口

| 功能 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 统一 Agent 执行 | POST | `/api/agent/run` | Supervisor 自动路由并返回结构化结果 |
| 统一 Agent 流 | POST | `/api/agent/run_stream` | 统一 SSE envelope 和执行时间线 |
| 查询运行 | GET | `/api/agent/runs/{run_id}` | 查询已完成运行 |
| 查询 Trace | GET | `/api/agent/runs/{run_id}/trace` | 查询 Agent、工具事件与单次运行指标 |
| 聚合指标 | GET | `/api/agent/metrics` | 失败率、超时率、平均耗时、调度/工具/步骤数 |
| 列出个人线程 | GET | `/api/agent/threads` | 当前登录用户的线程摘要 |
| 查询线程 | GET | `/api/agent/threads/{thread_id}` | 查询持久化运行和用户可见消息 |
| 清理线程 | DELETE | `/api/agent/threads/{thread_id}` | 清理线程运行记录和图 Checkpoint |
| 旧普通对话 | POST | `/api/chat` | 兼容适配到统一 Agent Service |
| 旧流式对话 | POST | `/api/chat_stream` | 兼容旧 content/done 事件 |
| AIOps 兼容入口 | POST | `/api/aiops` | 已标记 deprecated，暂用 Task Agent |
| 文件上传 | POST | `/api/upload` | 上传并索引文档 |
| 健康检查 | GET | `/health` | 服务状态检查 |

除健康检查和登录入口外，上述业务接口均要求 Bearer Token；文件上传和目录索引还要求
`admin` 角色。浏览器端只在 `sessionStorage` 保存短期访问令牌，会话列表和消息均由服务端
按当前用户返回。

### 使用示例

```bash
# 登录（由管理员通过 CLI 创建本地账号）
TOKEN=$(curl -s -X POST "http://localhost:9900/api/auth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=alice&password=your-password" | jq -r .access_token)

# 统一 JSON 接口；不传 user_id，thread_id 可省略以创建新线程
curl -X POST "http://localhost:9900/api/agent/run" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"查询内部发布规范"}'

# 统一 SSE 接口
curl -X POST "http://localhost:9900/api/agent/run_stream" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"结合制度和当前数据给出分析"}' \
  --no-buffer

# 旧 Chat 兼容接口
curl -X POST "http://localhost:9900/api/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"Id":"thread-001","Question":"你好"}'

# 按 run_id 还原安全执行链路
curl "http://localhost:9900/api/agent/runs/<run_id>/trace"

# 查看运行聚合指标
curl "http://localhost:9900/api/agent/metrics"
```

统一 SSE 事件至少包含 `event_id`、`run_id`、`thread_id`、`type`、`timestamp`
和 `data`。每个流只产生一个 `run_completed` 或 `run_failed` 终态事件。浏览器断开后，
服务会关闭对应异步事件生成器，不再继续向该客户端生成事件。

默认使用 PostgreSQL 保存 Multi-Agent 顶层 LangGraph Checkpoint、运行索引、安全事件和按
`thread_id` 的案件工作记忆。`make init`、`make start`、`make dev` 与 `make run` 都会启动
PostgreSQL 并执行幂等 Schema 升级；首次使用前必须从 `.env.example` 创建 `.env`，并替换
`POSTGRES_PASSWORD` 与 `DATABASE_URL` 内的密码占位符。Task Agent 子图和旧 RAG 对话链路仍有
各自的状态边界，因此这不等于所有兼容链路都已持久化。未获得可靠 provider usage 与计价信息时，
模型调用数、Token 数和 Token 成本返回 `null`，不会伪装为 0。

## 📁 项目结构

```
Oncall_agent/
├── app/                                    # 应用核心
│   ├── __init__.py                         # 包初始化（自动加载日志配置）
│   ├── main.py                             # FastAPI 应用入口
│   ├── config.py                           # 配置管理（环境变量、MCP 服务器配置）
│   ├── api/                                # API 路由层
│   │   ├── __init__.py
│   │   ├── agent.py                        # 统一 Agent JSON/SSE 接口
│   │   ├── chat.py                         # 旧 Chat 兼容适配
│   │   ├── aiops.py                        # 已废弃标记的 Task 兼容入口
│   │   ├── file.py                         # 文件管理（文档上传）
│   │   └── health.py                       # 健康检查（服务状态）
│   ├── services/                           # 业务服务层
│   │   ├── __init__.py
│   │   ├── multi_agent_service.py          # 统一执行、事件与运行查询
│   │   ├── run_store.py                    # 内存/SQLite/PostgreSQL RunStore
│   │   ├── persistence.py                  # 成对创建 Checkpointer 与 RunStore
│   │   ├── event_service.py                # 安全 SSE 事件信封
│   │   ├── rag_agent_service.py            # 旧 RAG 服务（兼容期保留）
│   │   ├── aiops_service.py                # 旧 AIOps 到 Task Agent 的适配
│   │   ├── vector_store_manager.py         # 向量存储管理器
│   │   ├── vector_embedding_service.py     # 向量embedding服务
│   │   ├── vector_index_service.py         # 向量索引服务
│   │   ├── vector_search_service.py        # 向量检索服务
│   │   └── document_splitter_service.py    # 文档分割服务
│   ├── agent/                              # Agent 模块
│   │   ├── __init__.py
│   │   ├── graph.py                        # 顶层 Supervisor-Specialist 图
│   │   ├── state.py                        # 顶层状态与 Reducer
│   │   ├── contracts.py                    # 跨 Agent 结构化契约
│   │   ├── mcp_client.py                   # MCP 客户端（工具调用）
│   │   ├── supervisor/                     # Supervisor Agent
│   │   ├── specialists/                    # Knowledge/Data/Task/Reviewer
│   │   ├── runtime/                        # 限制与 Checkpointer 工厂
│   │   └── aiops/                          # 旧模块导入兼容层
│   ├── models/                             # 数据模型层
│   │   ├── __init__.py
│   │   ├── agent.py                        # 统一 API 和 SSE 模型
│   │   ├── aiops.py                        # AIOps 模型
│   │   ├── document.py                     # 文档模型
│   │   ├── request.py                      # 请求模型
│   │   └── response.py                     # 响应模型
│   ├── tools/                              # Agent 工具集
│   │   ├── __init__.py
│   │   ├── knowledge_tool.py               # 知识库查询工具
│   │   └── time_tool.py                    # 时间工具
│   ├── core/                               # 核心组件
│   │   ├── __init__.py
│   │   ├── llm_factory.py                  # LLM 工厂（模型管理）
│   │   └── milvus_client.py                # Milvus 客户端
│   └── utils/                              # 工具类
│       ├── __init__.py
│       └── logger.py                       # 日志配置（Loguru）
├── static/                                 # Web 前端（纯静态）
│   ├── index.html                          # 主页面
│   ├── app.js                              # 前端逻辑
│   └── styles.css                          # 样式表
├── mcp_servers/                            # MCP 服务器
│   ├── cls_server.py                       # CLS 日志查询服务
│   ├── monitor_server.py                   # 监控数据服务
│   ├── ticket_server.py                    # 只读脱敏业务工单服务
│   └── README.md                           # MCP 服务说明
├── aiops-docs/                             # 运维知识库（Markdown 文档）
├── business-docs/                          # 产品、规则、FAQ 和交付知识库
├── tests/evals/                            # 固定离线 Eval 数据集和测试
├── logs/                                   # 日志目录（Loguru 自动创建）
│   └── app_YYYY-MM-DD.log                  # 按天轮转的日志文件
├── uploads/                                # 上传文件临时目录
├── volumes/                                # Milvus 数据持久化目录
├── .env                                    # 环境变量配置（需手动创建）
├── Makefile                                # 项目管理命令（Linux/macOS）
├── start-windows.bat                       # Windows 启动脚本
├── stop-windows.bat                        # Windows 停止脚本
├── vector-database.yml                     # Milvus Docker Compose 配置
├── pyproject.toml                          # 项目配置（依赖、元数据）
├── uv.lock                                 # uv 依赖锁定文件
├── pyrightconfig.json                      # Pyright 类型检查配置
└── README.md                               # 项目说明
```

## ⚙️ 配置说明

通过 `.env` 文件配置：

```bash
# 阿里云LLM DashScope 配置（必填）
# 秘钥管理： https://bailian.console.aliyun.com/cn-beijing/?spm=5176.29597918.J_SEsSjsNv72yRuRFS2VknO.2.61ac133ccTVQLw&tab=demohouse#/api-key
DASHSCOPE_API_KEY=your-api-key （配置你自己的秘钥）
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen-max

# Milvus 配置
MILVUS_HOST=localhost
MILVUS_PORT=19530

# RAG 配置
RAG_TOP_K=3
CHUNK_MAX_SIZE=800
CHUNK_OVERLAP=100

# PostgreSQL 持久化（默认）
CHECKPOINTER_BACKEND=postgres
DATABASE_URL=postgresql://oncall_agent:<password>@localhost:5432/oncall_agent
POSTGRES_PASSWORD=<password>
POSTGRES_POOL_MIN_SIZE=1
POSTGRES_POOL_MAX_SIZE=10

# 仅在需要本地回退时显式切换：
# CHECKPOINTER_BACKEND=sqlite
# CHECKPOINT_PATH=data/multi_agent.sqlite3

# 非运维业务 MCP 示例
MCP_TICKET_URL=http://localhost:8005/mcp
```

### PostgreSQL 默认持久化

PostgreSQL 是默认后端。首次使用前从 `.env.example` 创建 `.env`，设置
`POSTGRES_PASSWORD`，并将相同密码写入 `DATABASE_URL`，然后执行：

```bash
make postgres-up       # 启动 PostgreSQL 容器
make db-upgrade        # 初始化业务表、工作记忆表和 LangGraph Checkpoint 表
make postgres-status
```

`make init`、`make start`、`make dev` 与 `make run` 会执行上述 PostgreSQL 启动和幂等升级；
无需单独运行它们。

数据库不可达或 Schema 尚未初始化时，应用会明确启动失败，不会静默退回 SQLite。历史公开
run、event 和 Trace 可在停止新写入并备份 SQLite 后迁移：

```bash
make db-migrate-sqlite SQLITE_PATH=data/multi_agent.sqlite3
```

迁移命令只读 SQLite 源文件并幂等 upsert PostgreSQL，不删除原文件，也不复制 LangGraph
内部 Checkpoint 表。切换前应等待正在执行的 run 进入终态。

## 旧 AIOps 兼容入口

`/api/aiops` 已在 OpenAPI 中标记为 deprecated。它把固定运维请求适配到通用 Task Agent，
用于迁移期间兼容旧调用方；新功能应使用 `/api/agent/run` 或 `/api/agent/run_stream`。

### 兼容性测试

```bash
# 服务已通过 make init 自动启动
# 如需重启服务：make restart

curl -X POST "http://localhost:9900/api/aiops" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test"}' \
  --no-buffer
```

## 📝 开发指南

### 常用命令

```bash
# 项目管理
make init              # 一键初始化（Docker + 服务 + 文档）
make start             # 启动所有服务
make stop              # 停止所有服务
make restart           # 重启所有服务

# 依赖管理
make install-dev       # 安装开发依赖
make sync              # 同步依赖

# Docker 管理
make up                # 启动 Docker 容器
make down              # 停止 Docker 容器
make postgres-up       # 启动默认 PostgreSQL
make postgres-down     # 停止 PostgreSQL
make db-upgrade        # 初始化/升级 PostgreSQL Schema

# 代码质量
make format            # 格式化代码
make lint              # 代码检查
make type-check         # 类型检查
make test-quick         # 全部自动化测试
make eval               # 固定数据集评估，报告写入 artifacts/evals/latest.json
```


## 🐛 常见问题

### Windows 环境问题

#### 1. `make` 命令不可用
Windows 不支持 `make` 命令，请使用提供的批处理脚本：
```powershell
# 启动服务
.\start-windows.bat

# 停止服务
.\stop-windows.bat
```

#### 2. PowerShell 执行策略限制
如果遇到 "无法加载文件，因为在此系统上禁止运行脚本" 错误：
```powershell
# 临时允许脚本执行（管理员权限）
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process

# 或者使用 CMD 而不是 PowerShell
cmd
.\start-windows.bat
```

#### 3. 端口被占用（Windows）
```powershell
# 查看占用端口的进程
netstat -ano | findstr :9900

# 结束进程（替换 PID 为实际进程 ID）
taskkill /F /PID <PID>
```

### 通用问题

### API Key 错误
```bash
# 仅检查变量是否已设置，不打印密钥内容
python -c "import os; print(bool(os.getenv('DASHSCOPE_API_KEY')))"
```

### Milvus 连接失败
```bash
# 确保本机有 Docker 服务并且已经启动（可以使用 Docker Desktop）

# 检查 Milvus 状态
docker ps | grep milvus

# 重启 Milvus（使用 docker compose）
docker compose -f vector-database.yml restart

# 或者重启单个服务
docker compose -f vector-database.yml restart standalone
```

### 服务无法启动

**Linux/macOS:**
```bash
# 查看服务日志
tail -f logs/app_$(date +%Y-%m-%d).log  # FastAPI 主服务（Loguru 日志）
tail -f mcp_cls.log                      # CLS MCP 服务
tail -f mcp_monitor.log                  # Monitor MCP 服务
tail -f mcp_ticket.log                   # Business Ticket MCP 服务

# 检查端口占用
lsof -i :9900  # FastAPI
lsof -i :8003  # CLS MCP
lsof -i :8004  # Monitor MCP
lsof -i :8005  # Business Ticket MCP
```

**Windows:**
```powershell
# 查看服务日志（获取今天的日期）
$today = Get-Date -Format "yyyy-MM-dd"
type logs\app_$today.log  # FastAPI 主服务（Loguru 日志）
type mcp_cls.log          # CLS MCP 服务
type mcp_monitor.log      # Monitor MCP 服务

# 或者查看最新的日志文件
Get-ChildItem logs\*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content -Tail 50

# 检查端口占用
netstat -ano | findstr :9900  # FastAPI
netstat -ano | findstr :8003  # CLS MCP
netstat -ano | findstr :8004  # Monitor MCP
```

## 📚 参考资源

- [FastAPI 文档](https://fastapi.tiangolo.com/)
- [LangChain 文档](https://python.langchain.com/)
- [LangGraph Plan-Execute](https://langchain-ai.github.io/langgraph/tutorials/plan-and-execute/)
- [阿里云 DashScope](https://dashscope.aliyun.com/)
- [MCP 协议](https://modelcontextprotocol.io/)

## 📄 许可证
author： chief

MIT License
