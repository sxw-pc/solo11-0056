"""基于标准库 html.parser 的轻量 DOM。

不执行 JavaScript，不构造 XPath。每个节点保留源偏移量与起始/结束行号，
供表格区域识别和证据切片使用。
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

ALLOWED_TAGS = frozenset(
    {
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "caption",
        "a",
        "span",
        "br",
        "p",
        "div",
        "colgroup",
        "col",
    }
)
VOID_TAGS = frozenset({"br", "col"})
SKIP_CONTENT_TAGS = frozenset({"script", "style"})
BLOCK_SEPARATOR_TAGS = frozenset({"p", "div", "tr", "td", "th", "br"})


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    text: str = ""
    line_no: int = 0
    end_line: int = 0
    start_offset: int = 0
    end_offset: int = 0
    parent: "Node | None" = field(default=None, repr=False, compare=False)

    def add_child(self, child: "Node") -> None:
        child.parent = self
        self.children.append(child)

    def find_all(self, tag: str) -> list["Node"]:
        """返回后代中所有指定标签（不含节点自身）。"""
        found: list[Node] = []
        for child in self.children:
            if child.tag == tag:
                found.append(child)
            found.extend(child.find_all(tag))
        return found

    def first_child_of_type(self, tag: str) -> "Node | None":
        for child in self.children:
            if child.tag == tag:
                return child
        return None

    def child_tags(self) -> list[str]:
        return [child.tag for child in self.children]

    def text_content(self, skip_tags: frozenset[str] = frozenset()) -> str:
        """拼接文本；表格单元格可用 skip_tags={'table'} 排除内层表格。"""
        parts: list[str] = []

        def walk(node: Node) -> None:
            if node.tag == "#text":
                parts.append(node.text)
                return
            if node.tag in skip_tags:
                return
            if node.tag in BLOCK_SEPARATOR_TAGS:
                parts.append(" ")
            for child in node.children:
                walk(child)
            if node.tag in BLOCK_SEPARATOR_TAGS:
                parts.append(" ")

        walk(self)
        return "".join(parts)

    def source_slice(self, source: str) -> str:
        start = max(0, self.start_offset)
        end = self.end_offset if self.end_offset > start else len(source)
        return source[start:end]


class _TreeBuilder(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.line_starts = [0]
        for index, char in enumerate(source):
            if char == "\n":
                self.line_starts.append(index + 1)
        self.root = Node("#document")
        self.stack: list[Node] = [self.root]
        self.suppressed = 0

    def _absolute(self, line: int, column: int) -> int:
        index = min(max(line - 1, 0), len(self.line_starts) - 1)
        return self.line_starts[index] + max(column, 0)

    def _line_at(self, offset: int) -> int:
        return bisect_right(self.line_starts, max(offset, 0))

    def _make_node(self, tag: str, attrs: list[tuple[str, str | None]], start: int) -> Node:
        normalized = {key.lower(): (value or "") for key, value in attrs}
        return Node(
            tag=tag,
            attrs=normalized,
            line_no=self._line_at(start),
            start_offset=start,
        )

    def _close_at(self, node: Node, end_offset: int) -> None:
        node.end_offset = max(node.start_offset, end_offset)
        node.end_line = self._line_at(max(node.end_offset - 1, node.start_offset))

    def _implicit_close(self, new_tag: str) -> None:
        closing: set[str] = set()
        if new_tag == "tr":
            closing = {"tr", "td", "th"}
        elif new_tag in {"td", "th"}:
            closing = {"td", "th"}
        elif new_tag in {"thead", "tbody", "tfoot"}:
            closing = {"thead", "tbody", "tfoot"}
        if not closing:
            return
        for index in range(len(self.stack) - 1, 0, -1):
            node = self.stack[index]
            if node.tag in closing:
                self._close_at(node, self._absolute(*self.getpos()))
                del self.stack[index]
                if node.tag == "tr":
                    break

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in SKIP_CONTENT_TAGS:
            self.suppressed += 1
            return
        line, column = self.getpos()
        start = self._absolute(line, column)
        if tag not in ALLOWED_TAGS:
            return
        self._implicit_close(tag)
        node = self._make_node(tag, attrs, start)
        self.stack[-1].add_child(node)
        if tag in VOID_TAGS:
            raw = self.get_starttag_text() or f"<{tag}>"
            self._close_at(node, start + len(raw))
        else:
            self.stack.append(node)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        line, column = self.getpos()
        start = self._absolute(line, column)
        if tag in ALLOWED_TAGS:
            node = self._make_node(tag, attrs, start)
            self.stack[-1].add_child(node)
            raw = self.get_starttag_text() or f"<{tag}/>"
            self._close_at(node, start + len(raw))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_CONTENT_TAGS:
            self.suppressed = max(0, self.suppressed - 1)
            return
        if self.suppressed or tag not in ALLOWED_TAGS:
            return
        line, column = self.getpos()
        start = self._absolute(line, column)
        gt = self.source.find(">", start)
        end = gt + 1 if gt >= 0 else start + len(tag) + 3
        for index in range(len(self.stack) - 1, 0, -1):
            node = self.stack[index]
            if node.tag == tag:
                self._close_at(node, end)
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self.suppressed or not data:
            return
        line, column = self.getpos()
        start = self._absolute(line, column)
        node = Node(
            tag="#text",
            text=data,
            line_no=self._line_at(start),
            start_offset=start,
            end_offset=start + len(data),
        )
        node.end_line = self._line_at(max(node.end_offset - 1, start))
        self.stack[-1].add_child(node)

    def finish(self) -> Node:
        end = len(self.source)
        for node in self.stack[1:]:
            self._close_at(node, end)
        return self.root


def parse_html(source: str) -> Node:
    """解析 HTML 字符串并返回文档根节点。"""
    builder = _TreeBuilder(source)
    builder.feed(source)
    builder.close()
    return builder.finish()


def parse_file(path: str | Path) -> tuple[str, Node]:
    """读取 UTF-8 HTML 文件并解析，返回原始文本与 DOM。"""
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    return source, parse_html(source)
