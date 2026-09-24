import hashlib
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


@dataclass(frozen=True)
class CloudTextChunk:
    index: int
    content: str
    content_hash: str


def chunk_image_description(description: str) -> list[CloudTextChunk]:
    remaining = description.strip()
    chunks: list[CloudTextChunk] = []
    max_chars = 320
    min_break = 160
    separators = ("\n\n", "\n", "。", "！", "？", ".", "!", "?", "；", ";", "，", ",", " ")

    while remaining:
        end = min(len(remaining), max_chars)
        if end < len(remaining):
            for separator in separators:
                boundary = remaining.rfind(separator, min_break, end)
                if boundary >= 0:
                    end = boundary + len(separator)
                    break
        content = remaining[:end].strip()
        if content:
            chunks.append(
                CloudTextChunk(
                    index=len(chunks),
                    content=content,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                )
            )
        remaining = remaining[end:].lstrip()
    return chunks


def document_text(content: str, content_format: str) -> str:
    if content_format == "html":
        parser = _TextParser()
        parser.feed(content)
        return "\n".join(parser.parts)
    try:
        root = json.loads(content)
    except json.JSONDecodeError:
        return content
    parts: list[str] = []

    def visit(node) -> None:
        if not isinstance(node, dict):
            return
        value = node.get("text")
        if isinstance(value, str):
            parts.append(value)
        children = node.get("content")
        if isinstance(children, list):
            for child in children:
                visit(child)
            if node.get("type") in {"paragraph", "heading", "listItem", "codeBlock"}:
                parts.append("\n")

    visit(root)
    return "".join(parts)


def chunk_document(title: str, content: str, content_format: str) -> list[CloudTextChunk]:
    normalized = re.sub(r"[ \t]+", " ", document_text(content, content_format))
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    if not normalized:
        return []
    text = f"{title.strip()}\n\n{normalized}" if title.strip() else normalized
    size, overlap = 480, 60
    chunks: list[CloudTextChunk] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        value = text[start:end].strip()
        if value:
            chunks.append(
                CloudTextChunk(
                    index=len(chunks),
                    content=value,
                    content_hash=hashlib.sha256(value.encode()).hexdigest(),
                )
            )
        if end == len(text):
            break
        start = end - overlap
    return chunks


def text_fingerprint(title: str, content: str, content_format: str) -> str:
    value = f"{title}\0{content_format}\0{content}"
    return hashlib.sha256(value.encode()).hexdigest()
