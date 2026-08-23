from app.agent.contracts import AgentResult, AgentTask, Evidence


def test_contract_mutable_defaults_are_isolated() -> None:
    first = AgentTask(task_id="one", objective="first")
    second = AgentTask(task_id="two", objective="second")
    first.constraints.append("read-only")

    result_one = AgentResult(
        agent_name="knowledge",
        task_id="one",
        status="success",
        summary="done",
    )
    result_two = AgentResult(
        agent_name="knowledge",
        task_id="two",
        status="success",
        summary="done",
    )
    result_one.evidence.append(
        Evidence(source_type="knowledge", source_name="doc", content="evidence")
    )

    assert second.constraints == []
    assert result_two.evidence == []
