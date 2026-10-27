@echo off
chcp 65001 >nul
echo ====================================
echo 停止企业知识服务多智能体平台
echo ====================================
echo.

REM 停止 FastAPI 服务
echo [1/5] 停止 FastAPI 服务...
taskkill /FI "WINDOWTITLE eq Enterprise Knowledge Agent API*" /F >nul 2>&1
if errorlevel 1 (
    echo [信息] FastAPI 服务未运行或已停止
) else (
    echo [成功] FastAPI 服务已停止
)
echo.

REM 停止 CLS MCP 服务
echo [2/5] 停止 CLS MCP 服务...
taskkill /FI "WINDOWTITLE eq CLS MCP Server*" /F >nul 2>&1
if errorlevel 1 (
    echo [信息] CLS MCP 服务未运行或已停止
) else (
    echo [成功] CLS MCP 服务已停止
)
echo.

REM 停止 Monitor MCP 服务
echo [3/5] 停止 Monitor MCP 服务...
taskkill /FI "WINDOWTITLE eq Monitor MCP Server*" /F >nul 2>&1
if errorlevel 1 (
    echo [信息] Monitor MCP 服务未运行或已停止
) else (
    echo [成功] Monitor MCP 服务已停止
)
echo.

REM 停止 Business Ticket MCP 服务
echo [4/5] 停止 Business Ticket MCP 服务...
taskkill /FI "WINDOWTITLE eq Business Ticket MCP Server*" /F >nul 2>&1
if errorlevel 1 (
    echo [信息] Business Ticket MCP 服务未运行或已停止
) else (
    echo [成功] Business Ticket MCP 服务已停止
)
echo.

REM 停止 Docker 容器
echo [5/6] 停止 Milvus 容器...
docker ps --format "{{.Names}}" | findstr "milvus" >nul 2>&1
if not errorlevel 1 (
    docker compose -f vector-database.yml down
    if errorlevel 1 (
        echo [错误] Docker 容器停止失败
    ) else (
        echo [成功] Milvus 容器已停止
    )
) else (
    echo [信息] Milvus 容器未运行
)
echo.

REM 停止 PostgreSQL 容器（保留数据卷）
echo [6/6] 停止 PostgreSQL 容器...
docker compose -f postgres-database.yml down
if errorlevel 1 (
    echo [错误] PostgreSQL 容器停止失败
) else (
    echo [成功] PostgreSQL 容器已停止
)
echo.

echo ====================================
echo 所有服务已停止！
echo ====================================
echo.
echo 提示:
echo   - 如需完全清理 Docker 数据卷，运行:
echo     docker compose -f vector-database.yml down -v
echo.
pause
