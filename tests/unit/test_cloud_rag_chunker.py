import json

from app.services.cloud_rag_chunker import (
    chunk_document,
    document_text,
    passage_text,
    text_fingerprint,
)


def test_extracts_tiptap_text_and_chunks_long_documents():
    content = (
        '{"type":"doc","content":[{"type":"heading","content":'
        '[{"type":"text","text":"标题"}]},{"type":"paragraph","content":'
        '[{"type":"text","text":"' + "正文" * 600 + '"}]}]}'
    )

    chunks = chunk_document("文章", content, "tiptap_json")

    assert "标题" in document_text(content, "tiptap_json")
    assert len(chunks) > 1
    assert all(
        len(passage_text("文章", chunk.heading_path, chunk.content)) <= 320 for chunk in chunks
    )
    assert all(chunk.heading_path == ["标题"] for chunk in chunks)


def test_structured_blocks_keep_heading_context_and_readable_content():
    content = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [{"type": "text", "text": "架构"}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "第一段说明数据库如何保存文档。" * 4}],
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "列表中的同步策略"}],
                                }
                            ],
                        }
                    ],
                },
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "检索"}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "第二节介绍向量查询与引用。" * 5}],
                },
            ],
        },
        ensure_ascii=False,
    )

    chunks = chunk_document("设计文档", content, "tiptap_json")

    assert chunks[0].heading_path == ["架构"]
    assert "列表中的同步策略" in chunks[0].content
    assert chunks[-1].heading_path == ["架构", "检索"]
    assert chunks[0].content.startswith("第一段")
    assert passage_text("设计文档", chunks[0].heading_path, chunks[0].content).startswith(
        "设计文档\n架构\n"
    )
    assert all(len(passage_text("设计文档", c.heading_path, c.content)) <= 320 for c in chunks)


def test_long_prefix_and_unbroken_body_stay_within_passage_budget():
    content = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [{"type": "text", "text": "标题" * 100}],
                },
                {"type": "paragraph", "content": [{"type": "text", "text": "正文" * 600}]},
            ],
        },
        ensure_ascii=False,
    )
    title = "文档" * 100

    chunks = chunk_document(title, content, "tiptap_json")

    assert len(chunks) > 1
    assert all(len(passage_text(title, c.heading_path, c.content)) <= 320 for c in chunks)
    assert "".join(chunk.content for chunk in chunks) == "正文" * 600


def test_short_memo_and_legacy_html():
    memo = json.dumps(
        {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "备忘录内容共十字"}]}
            ],
        },
        ensure_ascii=False,
    )

    assert len(chunk_document("便签", memo, "tiptap_json", "memo")) == 1
    assert chunk_document("便签", memo, "tiptap_json") == []
    html = (
        "<h1>历史章节</h1><p>这段历史文档正文足够长，可以建立一条用于检索的语义索引。"
        "其中记录了数据同步、向量检索和文档引用的设计细节。</p>"
    )
    chunks = chunk_document("旧文档", html, "html")
    assert chunks[0].heading_path == ["历史章节"]
    assert chunks[0].content.startswith("这段历史文档正文")


def test_fingerprint_changes_with_document_revision_content():
    first = text_fingerprint("标题", "<p>第一版</p>", "html")
    second = text_fingerprint("标题", "<p>第二版</p>", "html")

    assert first != second
