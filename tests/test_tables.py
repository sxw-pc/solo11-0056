from dataclasses import replace
from pathlib import Path

from schema_extractor.dom import parse_html
from schema_extractor.tables import collect_table_nodes, extract_tables

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "fixtures" / "inputs"
MULTILEVEL = ROOT / "fixtures" / "multilevel"


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
    assert table.header_names == ["区域", "指标/销售额", "指标/同比", "日期"]
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


def _multilevel_candidate(name: str, config):
    source = (MULTILEVEL / name).read_text(encoding="utf-8")
    return extract_tables(parse_html(source), config.table_selector)[0]


def test_multilevel_header_pushes_group_name_to_each_child(default_config):
    table = _multilevel_candidate("page_07_multilevel.html", default_config)
    assert table.accepted
    assert table.header_names == ["项目", "金额/上限", "金额/下限", "备注"]
    assert table.unnamed_headers == [False, False, False, False]
    assert table.column_count == 4
    assert table.data_rows[0] == ["示例甲", "1,000", "100", "虚构数据"]
    assert table.data_rows[1] == ["示例乙", "2,000", "200", ""]


def test_multilevel_header_keeps_rowspan_group_on_its_column(default_config):
    table = _multilevel_candidate("page_07_multilevel.html", default_config)
    # rowspan 覆盖位不会把“项目”重复成 “项目/项目”。
    assert table.header_names[0] == "项目"
    assert table.header_names[3] == "备注"


def test_multilevel_empty_child_belongs_to_parent_group(default_config):
    source = (
        "<table><thead>"
        "<tr><th colspan=\"2\">金额</th><th>备注</th></tr>"
        "<tr><th>上限</th><th></th></tr>"
        "</thead><tbody><tr><td>1</td><td>2</td><td>3</td></tr></tbody></table>"
    )
    table = extract_tables(parse_html(source), default_config.table_selector)[0]
    assert table.header_names == ["金额/上限", "金额", "备注"]
    assert table.unnamed_headers == [False, False, False]


def test_multilevel_empty_children_get_unique_parent_paths(default_config):
    source = (
        "<table><thead>"
        "<tr><th colspan=\"2\">金额</th></tr>"
        "<tr><th></th><th></th></tr>"
        "</thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>"
    )
    table = extract_tables(parse_html(source), default_config.table_selector)[0]
    assert table.header_names == ["金额", "金额_2"]
    assert table.unnamed_headers == [False, False]


def test_multilevel_group_applies_only_to_columns_it_covers(default_config):
    source = (
        "<table><thead>"
        "<tr><th>地区</th><th colspan=\"2\">指标</th></tr>"
        "<tr><th></th><th>销售额</th><th>同比</th></tr>"
        "</thead><tbody><tr><td>a</td><td>1</td><td>2</td></tr></tbody></table>"
    )
    table = extract_tables(parse_html(source), default_config.table_selector)[0]
    assert table.header_names == ["地区", "指标/销售额", "指标/同比"]
    assert table.unnamed_headers == [False, False, False]


def test_multilevel_nonempty_group_with_empty_children_keeps_group_name(default_config):
    source = (
        "<table><thead>"
        "<tr><th>地区</th><th colspan=\"2\">指标</th></tr>"
        "<tr><th>销售额</th><th>同比</th></tr>"
        "</thead><tbody><tr><td>a</td><td>1</td><td>2</td></tr></tbody></table>"
    )
    table = extract_tables(parse_html(source), default_config.table_selector)[0]
    # 第二行缺失的网格位保持空白，列名沿用上级分组，不臆造子名。
    assert table.header_names == ["地区/销售额", "指标/同比", "指标"]
    assert table.column_count == 3


def test_single_row_header_output_is_unchanged(default_config):
    source = (
        "<table><thead><tr><th>编号</th><th>金额</th><th>金额</th></tr></thead>"
        "<tbody><tr><td>A-1</td><td>1</td><td>2</td></tr></tbody></table>"
    )
    table = extract_tables(parse_html(source), default_config.table_selector)[0]
    assert table.header_names == ["编号", "金额", "金额_2"]
    assert table.unnamed_headers == [False, False, False]


def test_single_row_header_col_fallback_order_is_preserved(default_config):
    source = (
        "<table><thead><tr><th></th><th>值</th><th></th></tr></thead>"
        "<tbody><tr><td>甲</td><td>1</td><td>乙</td></tr></tbody></table>"
    )
    table = extract_tables(parse_html(source), default_config.table_selector)[0]
    assert table.header_names == ["col_1", "值", "col_3"]


def test_multilevel_three_rows_full_path(default_config):
    source = (
        "<table><thead>"
        "<tr><th rowspan=\"3\">项目</th><th colspan=\"4\">金额</th></tr>"
        "<tr><th colspan=\"2\">收入</th><th colspan=\"2\">支出</th></tr>"
        "<tr><th>上限</th><th>下限</th><th>上限</th><th>下限</th></tr>"
        "</thead><tbody><tr><td>a</td><td>1</td><td>2</td><td>3</td><td>4</td>"
        "</tr></tbody></table>"
    )
    table = extract_tables(parse_html(source), default_config.table_selector)[0]
    assert table.header_names == [
        "项目",
        "金额/收入/上限",
        "金额/收入/下限",
        "金额/支出/上限",
        "金额/支出/下限",
    ]
    assert table.column_count == 5
    assert table.data_rows[0] == ["a", "1", "2", "3", "4"]


def test_multilevel_duplicate_group_paths_are_uniquified(default_config):
    source = (
        "<table><thead>"
        "<tr><th colspan=\"2\">金额</th><th colspan=\"2\">金额</th></tr>"
        "<tr><th>上限</th><th>下限</th><th>上限</th><th>下限</th></tr>"
        "</thead><tbody><tr><td>1</td><td>2</td><td>3</td><td>4</td></tr>"
        "</tbody></table>"
    )
    table = extract_tables(parse_html(source), default_config.table_selector)[0]
    assert table.header_names == [
        "金额/上限",
        "金额/下限",
        "金额/上限_2",
        "金额/下限_2",
    ]
    assert table.data_rows[0] == ["1", "2", "3", "4"]


def test_header_paths_accept_plain_string_grid(default_config):
    from schema_extractor.tables import _header_names

    names, unnamed = _header_names(
        [["金额", "金额", "备注"], ["上限", "下限", None]], 3
    )
    assert names == ["金额/上限", "金额/下限", "备注"]
    assert unnamed == [False, False, False]


def test_multilevel_path_name_is_used_for_record_values(default_config):
    table = _multilevel_candidate("page_07_multilevel.html", default_config)
    from schema_extractor.processing import process_table

    result, _ = process_table(
        default_config,
        "page_07_multilevel.html",
        (MULTILEVEL / "page_07_multilevel.html").read_text(encoding="utf-8"),
        table,
    )
    assert list(result.records[0]["values"]) == [
        "项目",
        "金额/上限",
        "金额/下限",
        "备注",
    ]
    assert result.records[0]["values"]["金额/上限"] == "1000"
    assert [column.name for column in result.columns] == table.header_names
