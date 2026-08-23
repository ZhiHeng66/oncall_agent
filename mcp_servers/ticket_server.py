"""Read-only MCP example for synthetic customer-support ticket data."""

from typing import Any

from fastmcp import FastMCP

mcp = FastMCP("BusinessTicket")

TICKETS: tuple[dict[str, Any], ...] = (
    {
        "ticket_id": "TKT-1001",
        "product": "KnowledgeHub Enterprise",
        "status": "open",
        "priority": "high",
        "category": "delivery",
        "summary": "交付环境尚未完成单点登录联调",
        "updated_at": "2026-08-22T09:30:00+08:00",
    },
    {
        "ticket_id": "TKT-1002",
        "product": "KnowledgeHub Enterprise",
        "status": "resolved",
        "priority": "medium",
        "category": "billing",
        "summary": "客户询问续费账单开票周期",
        "updated_at": "2026-08-21T16:10:00+08:00",
    },
    {
        "ticket_id": "TKT-1003",
        "product": "AgentFlow Studio",
        "status": "pending",
        "priority": "low",
        "category": "product",
        "summary": "等待客户补充工作流导入文件",
        "updated_at": "2026-08-23T11:45:00+08:00",
    },
)


def get_ticket_record(ticket_id: str) -> dict[str, Any]:
    """Return one synthetic ticket or an explicit not-found result."""

    normalized = ticket_id.strip().upper()
    for ticket in TICKETS:
        if ticket["ticket_id"] == normalized:
            return {"found": True, "ticket": dict(ticket)}
    return {"found": False, "ticket_id": normalized, "error": "ticket_not_found"}


def search_ticket_records(
    status: str | None = None,
    product: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Filter synthetic tickets with a hard response limit."""

    safe_limit = min(max(limit, 1), 20)
    normalized_status = status.strip().lower() if status else None
    normalized_product = product.strip().lower() if product else None
    matches = [
        dict(ticket)
        for ticket in TICKETS
        if (normalized_status is None or ticket["status"] == normalized_status)
        and (
            normalized_product is None
            or normalized_product in str(ticket["product"]).lower()
        )
    ][:safe_limit]
    return {"count": len(matches), "tickets": matches, "limit": safe_limit}


@mcp.tool()
def get_ticket(ticket_id: str) -> dict[str, Any]:
    """按工单编号查询一条只读、脱敏的示例业务工单。"""

    return get_ticket_record(ticket_id)


@mcp.tool()
def search_tickets(
    status: str | None = None,
    product: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """按状态或产品筛选只读、脱敏的示例业务工单，最多返回 20 条。"""

    return search_ticket_records(status=status, product=product, limit=limit)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8005, path="/mcp")
