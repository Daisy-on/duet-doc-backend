from app.services.cloud_rag_chunker import chunk_document, document_text, text_fingerprint


def test_extracts_tiptap_text_and_chunks_long_documents():
    content = (
        '{"type":"doc","content":[{"type":"heading","content":'
        '[{"type":"text","text":"标题"}]},{"type":"paragraph","content":'
        '[{"type":"text","text":"' + "正文" * 600 + '"}]}]}'
    )

    chunks = chunk_document("文章", content, "tiptap_json")

    assert "标题" in document_text(content, "tiptap_json")
    assert len(chunks) > 1
    assert all(len(chunk.content) <= 480 for chunk in chunks)
    assert len({chunk.content_hash for chunk in chunks}) == len(chunks)


def test_fingerprint_changes_with_document_revision_content():
    first = text_fingerprint("标题", "<p>第一版</p>", "html")
    second = text_fingerprint("标题", "<p>第二版</p>", "html")

    assert first != second
