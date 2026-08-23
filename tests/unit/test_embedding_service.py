import pytest

from app.services.vector_embedding_service import DashScopeEmbeddings


def test_embedding_service_defers_key_validation_until_provider_call() -> None:
    embeddings = DashScopeEmbeddings(api_key="")

    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        embeddings.embed_query("test")
