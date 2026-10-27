from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_frontend_uses_unified_agent_entry_and_timeline() -> None:
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "class SSEEventParser" in script
    assert "/agent/run`" in script
    assert "/agent/run_stream`" in script
    assert "sendAgentStreamMessage" in script
    assert "appendTimelineEvent" in script
    assert "renderAgentResult" in script
    assert "formatAgentAnswer" in script
    assert "humanizeAgentError" in script
    assert "renderAgentSources" in script
    assert "企业知识服务多智能体平台" in page
    assert 'id="aiOpsSidebarBtn"' not in page


def test_frontend_handles_all_public_terminal_events() -> None:
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "event.type === 'run_completed'" in script
    assert "event.type === 'run_failed'" in script
    assert "响应流未返回终态事件" in script


def test_frontend_uses_session_auth_and_server_owned_threads() -> None:
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'id="loginForm"' in page
    assert 'id="logoutBtn"' in page
    assert "[hidden]" in styles
    assert "/auth/token" in script
    assert "/auth/me" in script
    assert "sessionStorage.setItem('accessToken'" in script
    assert "sessionStorage.removeItem('accessToken')" in script
    assert "authorizedFetch" in script
    assert "Authorization" in script
    assert "response.status === 401" in script
    assert "/agent/threads" in script
    assert "localStorage.getItem" not in script
    assert "localStorage.setItem" not in script
    assert "history.messages" not in script
    assert "user_id" not in script


def test_frontend_groups_timeline_errors_and_sources_for_readability() -> None:
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "timeline-events" in script
    assert "agent-result-meta" in script
    assert "agent-errors" in script
    assert "source-excerpt" in script
    assert ".agent-errors" in styles
    assert ".source-card > summary" in styles
