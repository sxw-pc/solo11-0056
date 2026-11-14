from dataclasses import replace
from pathlib import Path

from schema_extractor.dom import parse_html
from schema_extractor.tables import collect_table_nodes, extract_tables

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "fixtures" / "inputs"


def _candidates(name, config):
    source = (INPUTS / name).read_text(encoding="utf-8")
    document = parse_html(source)
    return document, extract_tables(document, config.table_selector)


def test_simple_table_header_and_caption(default_config):
    _, candidates = _candidates("page_01.html", default_config)
    assert len(candidates) == 1
    table = candidates[0]
    assert table.accepted
    assert table.caption == "示例合同清单"
    assert table.header_names == ["项目编号", "项目名称", "合同金额", "完成率", "更新日期"]
    assert table.unnamed_headers == [False] * 5
    assert table.column_count == 5
    assert table.row_count == 4


def test_rowspan_and_colspan_expand_to_rectangle(default_config):
    _, candidates = _candidates("page_02.html", default_config)
    table = candidates[0]
    assert table.header_names == ["区域", "销售额", "同比", "日期"]
    assert table.column_count == 4
    assert len(table.data_rows) == 3
    assert table.data_rows[0][0] == "华东"
    assert table.data_rows[1][0] == "华东"
    assert table.data_rows[2][2] == "0%"
    assert all(len(row) == 4 for row in table.data_rows)


def test_nested_table_is_skipped_by_default(default_config):
    _, candidates = _candidates("page_03_nested.html", default_config)
    assert len(candidates) == 2
    assert candidates[0].accepted
    assert candidates[1].filtered_reason == "nested_table_skipped"
    assert "nested_table_skipped" in candidates[1].notes
    assert candidates[0].data_rows[1][0] == "外层乙"


def test_nested_table_flatten_policy(default_config):
    selector = replace(default_config.table_selector, nested_table_policy="flatten")
    source = (INPUTS / "page_03_nested.html").read_text(encoding="utf-8")
    candidates = extract_tables(parse_html(source), selector)
    assert len(candidates) == 2
    assert all(table.accepted for table in candidates)
    assert "nested_table_flattened" in candidates[1].notes
    assert candidates[1].header_names == ["内层字段", "内层值"]


def test_messy_header_hint_and_deduplication(default_config):
    _, candidates = _candidates("page_04_messy.html", default_config)
    table = candidates[0]
    assert table.accepted
    assert table.header_names == ["编号", "名称", "col_3", "金额", "金额_2", "日期"]
    assert table.unnamed_headers == [False, False, True, False, False, False]
    assert table.data_rows[0][1] == "示例 记录 一"
    assert table.data_rows[2][1] == "示例记录三 补充说明"


def test_filter_reasons(default_config):
    _, candidates = _candidates("page_06_mixed.html", default_config)
    reasons = {table.table_index: table.filtered_reason for table in candidates}
    assert reasons == {
        0: "below_min_columns",
        1: None,
        2: "missing_header",
    }


def test_filter_by_min_rows(default_config):
    selector = replace(default_config.table_selector, min_rows=10)
    source = (INPUTS / "page_01.html").read_text(encoding="utf-8")
    table = extract_tables(parse_html(source), selector)[0]
    assert table.filtered_reason == "below_min_rows"


def test_require_header_false_accepts_headerless_table(default_config):
    selector = replace(default_config.table_selector, require_header=False)
    source = (INPUTS / "page_06_mixed.html").read_text(encoding="utf-8")
    candidates = extract_tables(parse_html(source), selector)
    table = candidates[2]
    assert table.accepted
    assert table.header_names == ["col_1", "col_2", "col_3"]
    assert table.data_rows[0] == ["甲", "乙", "丙"]


def test_no_table_page_yields_no_candidates(default_config):
    _, candidates = _candidates("page_05_no_table.html", default_config)
    assert candidates == []


def test_collect_table_nodes_marks_nested_children(default_config):
    source = (INPUTS / "page_03_nested.html").read_text(encoding="utf-8")
    nodes = collect_table_nodes(parse_html(source))
    assert [(node.attrs.get("class"), nested) for node, nested in nodes] == [
        ("outer", False),
        ("inner", True),
    ]


def test_empty_header_cells_are_named_by_position(default_config):
    source = (
        "<table><thead><tr><th></th><th>值</th></tr></thead>"
        "<tbody><tr><td>甲</td><td>1</td></tr></tbody></table>"
    )
    table = extract_tables(parse_html(source), default_config.table_selector)[0]
    assert table.header_names == ["col_1", "值"]
    assert table.unnamed_headers == [True, False]
