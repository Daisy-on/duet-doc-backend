import hashlib
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser

TARGET_CHUNK_CHARS = 280
MAX_PASSAGE_CHARS = 320
MIN_BREAK_CHARS = 140
SEPARATORS = ("\n", "。", "！", "？", ".", "!", "?", "；", ";", "，", ",", " ")
PLACEHOLDER = re.compile(r"开始书写你的内容(?:\.{3}|…)?")


def _normalize(value: str) -> str:
    return " ".join(value.split())


def passage_text(title: str, heading_path: list[str], content: str) -> str:
    prefix = "\n".join(
        value for value in (title.strip()[:60], " > ".join(heading_path)[:60]) if value
    )
    return "\n".join(value for value in (prefix, content) if value)


def _body_limit(title: str, heading_path: list[str]) -> int:
    prefix = passage_text(title, heading_path, "")
    return MAX_PASSAGE_CHARS - len(prefix) - bool(prefix)


def _split_long(value: str, limit: int) -> list[str]:
    parts = []
    remaining = value
    while remaining:
        end = min(len(remaining), limit)
        if end < len(remaining):
            for separator in SEPARATORS:
                boundary = remaining.rfind(separator, MIN_BREAK_CHARS, end)
                if boundary >= 0:
                    end = boundary + len(separator)
                    break
        part = remaining[:end].strip()
        if part:
            parts.append(part)
        remaining = remaining[end:].lstrip()
    return parts


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


class _BlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[tuple[str, list[str]]] = []
        self.heading_path: list[str] = []
        self.tag: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote"}:
            if self.tag is None:
                self.tag = tag
                self.parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag != self.tag:
            return
        value = _normalize("".join(self.parts))
        if value:
            if tag.startswith("h") and len(tag) == 2:
                level = int(tag[1])
                self.heading_path = self.heading_path[: level - 1] + [value]
            else:
                self.blocks.append((value, self.heading_path.copy()))
        self.tag = None
        self.parts = []

    def handle_data(self, data: str) -> None:
        if self.tag is not None:
            self.parts.append(data)


@dataclass(frozen=True)
class CloudTextChunk:
    index: int
    content: str
    content_hash: str
    heading_path: list[str]


def chunk_image_description(description: str) -> list[CloudTextChunk]:
    remaining = description.strip()
    chunks: list[CloudTextChunk] = []
    separators = ("\n\n", "\n", "。", "！", "？", ".", "!", "?", "；", ";", "，", ",", " ")
    while remaining:
        end = min(len(remaining), 320)
        if end < len(remaining):
            for separator in separators:
                boundary = remaining.rfind(separator, 160, end)
                if boundary >= 0:
                    end = boundary + len(separator)
                    break
        content = remaining[:end].strip()
        if content:
            chunks.append(
                CloudTextChunk(
                    len(chunks), content, hashlib.sha256(content.encode()).hexdigest(), []
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
        for child in node.get("content", []):
            visit(child)

    visit(root)
    return "".join(parts)


def _node_text(node: dict) -> str:
    if node.get("type") == "text":
        return node.get("text", "")
    if node.get("type") == "hardBreak":
        return "\n"
    if node.get("type") == "image":
        return ""
    return "".join(
        _node_text(child) for child in node.get("content", []) if isinstance(child, dict)
    )


def _tiptap_blocks(nodes: list, heading_path: list[str] | None = None):
    blocks: list[tuple[str, list[str]]] = []
    active = list(heading_path or [])
    for node in nodes:
        if not isinstance(node, dict):
            continue
        kind = node.get("type")
        if kind == "heading":
            level = int((node.get("attrs") or {}).get("level", 1))
            value = _normalize(_node_text(node))
            if value:
                active = active[: max(0, level - 1)] + [value]
        elif kind in {
            "paragraph",
            "codeBlock",
            "blockquote",
            "listItem",
            "tableCell",
            "tableHeader",
        }:
            value = _normalize(_node_text(node))
            if value:
                blocks.append((value, active.copy()))
        else:
            blocks.extend(_tiptap_blocks(node.get("content") or [], active))
    return blocks


def _extract_blocks(content: str, content_format: str):
    if content_format != "html":
        try:
            root = json.loads(content)
        except json.JSONDecodeError:
            root = None
        if isinstance(root, dict):
            return _tiptap_blocks(root.get("content") or [])
    parser = _BlockParser()
    parser.feed(content)
    if parser.blocks:
        return parser.blocks
    text_parser = _TextParser()
    text_parser.feed(content)
    value = _normalize(" ".join(text_parser.parts))
    return [(value, [])] if value else []


def chunk_document(
    title: str, content: str, content_format: str, source_type: str = "document"
) -> list[CloudTextChunk]:
    blocks = []
    for text, heading_path in _extract_blocks(content, content_format):
        text = _normalize(PLACEHOLDER.sub(" ", text))
        if text:
            blocks.append((text, heading_path))
    minimum = 8 if source_type == "memo" else 30
    if sum(ch.isalnum() for text, _ in blocks for ch in text) < minimum:
        return []

    packed: list[tuple[str, list[str]]] = []
    for text, heading_path in blocks:
        for part in _split_long(text, _body_limit(title, heading_path)):
            if packed and packed[-1][1] == heading_path:
                previous = packed[-1][0]
                if len(previous) + 1 + len(part) <= min(
                    TARGET_CHUNK_CHARS, _body_limit(title, heading_path)
                ):
                    packed[-1] = (f"{previous}\n{part}", heading_path)
                    continue
            packed.append((part, heading_path))
    return [
        CloudTextChunk(
            index,
            text,
            hashlib.sha256(passage_text(title, heading_path, text).encode()).hexdigest(),
            heading_path,
        )
        for index, (text, heading_path) in enumerate(packed)
    ]


def text_fingerprint(title: str, content: str, content_format: str) -> str:
    value = f"{title}\0{content_format}\0{content}"
    return hashlib.sha256(value.encode()).hexdigest()
