"""HTML 表格区域识别、表头判定与 rowspan/colspan 网格展开。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import TableSelector
from .dom import Node

_WHITESPACE = re.compile(r"\s+")
_SECTION_TAGS = frozenset({"thead", "tbody", "tfoot"})


@dataclass
class TableCandidate:
    table_index: int
    node: Node
    nested: bool
    caption: str
    header_names: list[str] = field(default_factory=list)
    unnamed_headers: list[bool] = field(default_factory=list)
    data_rows: list[list[str | None]] = field(default_factory=list)
    column_count: int = 0
    filtered_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.filtered_reason is None

    @property
    def row_count(self) -> int:
        return len(self.data_rows)


def collect_table_nodes(root: Node) -> list[tuple[Node, bool]]:
    """按文档顺序收集表格；第二个返回值表示它是否位于另一张表内。"""
    found: list[tuple[Node, bool]] = []

    def walk(node: Node, inside_table: bool) -> None:
        for child in node.children:
            if child.tag == "table":
                found.append((child, inside_table))
                walk(child, True)
            else:
                walk(child, inside_table)

    walk(root, False)
    return found


def _row_nodes(table: Node) -> list[tuple[Node, bool]]:
    rows: list[tuple[Node, bool]] = []
    for child in table.children:
        if child.tag == "tr":
            rows.append((child, False))
        elif child.tag in _SECTION_TAGS:
            for grandchild in child.children:
                if grandchild.tag == "tr":
                    rows.append((grandchild, child.tag == "thead"))
    return rows


def _cell_text(cell: Node) -> str:
    return _WHITESPACE.sub(" ", cell.text_content(skip_tags=frozenset({"table"}))).strip()


def _span(cell: Node, name: str, default: int = 1) -> int:
    raw = cell.attrs.get(name, "").strip()
    if not raw.isdigit():
        return default
    return max(1, min(int(raw), 1000))


def _build_grid(rows: list[Node]) -> list[list[str | None]]:
    """把行展开为矩形网格；rowspan/colspan 覆盖位复制值，缺失位为 None。"""
    grid: list[list[str | None]] = []
    pending: dict[int, tuple[int, str]] = {}
    for row in rows:
        cells: list[str | None] = []
        still_pending: dict[int, tuple[int, str]] = {}
        for column in sorted(pending):
            remaining, value = pending[column]
            while len(cells) <= column:
                cells.append(None)
            cells[column] = value
            if remaining > 1:
                still_pending[column] = (remaining - 1, value)
        pending = still_pending
        cursor = 0
        for cell in row.children:
            if cell.tag not in {"td", "th"}:
                continue
            while cursor < len(cells) and cells[cursor] is not None:
                cursor += 1
            colspan = _span(cell, "colspan")
            rowspan = _span(cell, "rowspan")
            value = _cell_text(cell)
            for offset in range(colspan):
                column = cursor + offset
                while len(cells) <= column:
                    cells.append(None)
                cells[column] = value
                if rowspan > 1:
                    pending[column] = (rowspan - 1, value)
            cursor += colspan
        grid.append(cells)
    width = max((len(row) for row in grid), default=0)
    for row in grid:
        row.extend([None] * (width - len(row)))
    return grid


def _rows_text(row: Node) -> str:
    return _WHITESPACE.sub(" ", row.text_content(skip_tags=frozenset({"table"}))).strip()


def _split_header_rows(
    table: Node, selector: TableSelector
) -> tuple[list[Node], list[Node], bool]:
    rows_with_section = _row_nodes(table)
    thead_rows = [row for row, in_thead in rows_with_section if in_thead]
    other_rows = [row for row, in_thead in rows_with_section if not in_thead]
    if thead_rows:
        return thead_rows, other_rows, True
    if not other_rows:
        return [], [], False
    first = other_rows[0]
    has_th = any(cell.tag == "th" for cell in first.children)
    hint_match = bool(selector.hint_pattern.search(_rows_text(first)))
    if has_th or hint_match:
        return [first], other_rows[1:], True
    return [], other_rows, False


def _caption(table: Node) -> str:
    caption = table.first_child_of_type("caption")
    if caption is None:
        return ""
    return _cell_text(caption)


def _header_names(header_grid: list[list[str | None]], width: int) -> tuple[list[str], list[bool]]:
    raw_names: list[str] = []
    unnamed: list[bool] = []
    for column in range(width):
        value = ""
        for row in header_grid:
            candidate = row[column] if column < len(row) else None
            if candidate is not None and candidate.strip():
                value = candidate
        raw_names.append(_WHITESPACE.sub(" ", value).strip())
        unnamed.append(not bool(value.strip()))
    names: list[str] = []
    used: dict[str, int] = {}
    for index, raw in enumerate(raw_names):
        base = raw or f"col_{index + 1}"
        count = used.get(base, 0) + 1
        used[base] = count
        name = base if count == 1 else f"{base}_{count}"
        while name in names:
            count += 1
            used[base] = count
            name = f"{base}_{count}"
        names.append(name)
    return names, unnamed


def _pad_rows(rows: list[list[str | None]], width: int) -> list[list[str | None]]:
    padded: list[list[str | None]] = []
    for row in rows:
        values = list(row[:width])
        values.extend([None] * (width - len(values)))
        if any(value is not None and value.strip() for value in values):
            padded.append(values)
    return padded


def _parse_candidate(
    node: Node,
    table_index: int,
    nested: bool,
    selector: TableSelector,
    notes: list[str],
) -> TableCandidate:
    header_rows, data_row_nodes, has_header = _split_header_rows(node, selector)
    header_grid = _build_grid(header_rows)
    data_grid = _build_grid(data_row_nodes)
    width = max(
        max((len(row) for row in header_grid), default=0),
        max((len(row) for row in data_grid), default=0),
    )
    data_rows = _pad_rows(data_grid, width)
    if has_header:
        names, unnamed = _header_names(header_grid, width)
    else:
        names = [f"col_{index + 1}" for index in range(width)]
        unnamed = [True] * width
    candidate = TableCandidate(
        table_index=table_index,
        node=node,
        nested=nested,
        caption=_caption(node),
        header_names=names,
        unnamed_headers=unnamed,
        data_rows=data_rows,
        column_count=width,
        notes=list(notes),
    )
    if width < selector.min_cols:
        candidate.filtered_reason = "below_min_columns"
    elif len(data_rows) < selector.min_rows:
        candidate.filtered_reason = "below_min_rows"
    elif selector.require_header and not has_header:
        candidate.filtered_reason = "missing_header"
    return candidate


def extract_tables(root: Node, selector: TableSelector) -> list[TableCandidate]:
    """识别并过滤文档中的表格，默认跳过内层嵌套表并记录 note。"""
    candidates: list[TableCandidate] = []
    for table_index, (node, nested) in enumerate(collect_table_nodes(root)):
        notes: list[str] = []
        if nested and selector.nested_table_policy == "skip":
            candidates.append(
                TableCandidate(
                    table_index=table_index,
                    node=node,
                    nested=True,
                    caption=_caption(node),
                    filtered_reason="nested_table_skipped",
                    notes=["nested_table_skipped"],
                )
            )
            continue
        if nested:
            notes.append("nested_table_flattened")
        candidates.append(
            _parse_candidate(node, table_index, nested, selector, notes)
        )
    return candidates
