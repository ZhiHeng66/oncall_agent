from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.agent.final_response import LLMFinalResponseGenerator


async def test_final_response_generator_streams_real_model_chunks_and_returns_full_answer() -> None:
    chunks: list[str] = []
    generator = LLMFinalResponseGenerator(FakeListChatModel(responses=["最终回答 [来源1]"]))

    answer = await generator.generate(
        "原始问题",
        "候选回答 [来源1]",
        event_sink=chunks.append,
    )

    assert len(chunks) > 1
    assert "".join(chunks) == answer
    assert answer == "最终回答 [来源1]"
