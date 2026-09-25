"""Emit cloud document chunks for the offline frontend/backend comparison."""

import json
import sys

from app.services.cloud_rag_chunker import chunk_document, passage_text


def main() -> None:
    document = json.load(sys.stdin)
    title = document["title"]
    content = document["content"]
    chunks = chunk_document(
        title,
        content,
        document.get("contentFormat", "tiptap_json"),
        document.get("sourceType", "document"),
    )
    result = [
        {
            "chunkIndex": chunk.index,
            "headingPath": chunk.heading_path,
            "content": chunk.content,
            "passage": passage_text(title, chunk.heading_path, chunk.content),
        }
        for chunk in chunks
    ]
    json.dump(result, sys.stdout, ensure_ascii=True)


if __name__ == "__main__":
    main()
