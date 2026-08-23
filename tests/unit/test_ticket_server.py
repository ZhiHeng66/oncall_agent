from mcp_servers.ticket_server import get_ticket_record, search_ticket_records


def test_get_ticket_record_is_read_only_and_reports_not_found() -> None:
    found = get_ticket_record("tkt-1001")
    missing = get_ticket_record("tkt-9999")

    assert found["found"] is True
    assert found["ticket"]["ticket_id"] == "TKT-1001"
    assert missing == {
        "found": False,
        "ticket_id": "TKT-9999",
        "error": "ticket_not_found",
    }


def test_search_ticket_records_filters_and_limits() -> None:
    result = search_ticket_records(status="open", product="knowledgehub", limit=100)

    assert result["limit"] == 20
    assert result["count"] == 1
    assert result["tickets"][0]["ticket_id"] == "TKT-1001"
