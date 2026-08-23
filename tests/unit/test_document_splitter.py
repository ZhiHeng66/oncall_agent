from app.services.document_splitter_service import DocumentSplitterService


def test_split_markdown_preserves_source_metadata() -> None:
    splitter = DocumentSplitterService()
    content = "# Runbook\n\n" + "CPU usage guidance. " * 120

    chunks = splitter.split_markdown(content, "docs/runbook.md")

    assert chunks
    assert all(chunk.metadata["_source"] == "docs/runbook.md" for chunk in chunks)
    assert all(chunk.metadata["_file_name"] == "runbook.md" for chunk in chunks)
    assert all(chunk.metadata["_extension"] == ".md" for chunk in chunks)


def test_split_empty_document_returns_no_chunks() -> None:
    splitter = DocumentSplitterService()

    assert splitter.split_document("  ", "docs/empty.txt") == []
