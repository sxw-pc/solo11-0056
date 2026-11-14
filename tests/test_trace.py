import json

import pytest

from schema_extractor.pipeline import build_run, execute_run
from schema_extractor.report import build_report
from schema_extractor.storage import Store
from schema_extractor.tables import extract_tables
from schema_extractor.trace import (
    INFERENCE_BRANCH_ORDER,
    build_trace_payload,
    not_evaluated_header_trace,
    write_trace,
)

FIXED_NOW = "2026-01-01T00:00:00Z"


def _trace(default_config):
    result = build_run(default_config, FIXED_NOW)
    return result, build_trace_payload(result)


def _table(trace, source_file, table_index):
    for file_entry in trace["files"]:
        if file_entry["source_file"] != source_file:
            continue
        for table in file_entry["tables"]:
            if table["table_index"] == table_index:
                return table
    raise AssertionError(f"未找到 {source_file}#{table_index}")


def test_trace_covers_every_detected_table(default_config):
    result, trace = _trace(default_config)
    assert trace["version"] == 1
    assert trace["run_id"] == result.run_id
    assert trace["generated_at"] == FIXED_NOW
    assert trace["counts"]["tables"] == result.tables_detected == 8
    assert trace["counts"]["tables_accepted"] == 5
    assert trace["counts"]["tables_filtered"] == 3
    assert trace["counts"]["tables_parsed"] == 7
    assert sum(len(f["tables"]) for f in trace["files"]) == 8


def test_filtered_tables_record_reason_and_notes(default_config):
    _, trace = _trace(default_config)
    nested = _table(trace, "page_03_nested.html", 1)
    assert nested["decision"] == "filtered"
    assert nested["filter_reason"] == "nested_table_skipped"
    assert nested["notes"] == ["nested_table_skipped"]
    # 未解析的嵌套表不产生列追踪与失败步骤。
    assert nested["parsed"] is False
    assert nested["columns"] == []
    assert nested["normalization"]["cells"] == 0
    assert nested["header_detection"] == not_evaluated_header_trace().to_dict()

    narrow = _table(trace, "page_06_mixed.html", 0)
    assert narrow["filter_reason"] == "below_min_columns"
    assert narrow["parsed"] is True
    assert narrow["header_detection"]["strategy"] == "thead"
    assert narrow["columns"] == []

    headerless = _table(trace, "page_06_mixed.html", 2)
    assert headerless["filter_reason"] == "missing_header"
    assert headerless["header_detection"]["strategy"] == "none"


def test_nested_flatten_policy_is_visible_in_notes(config_factory):
    config = config_factory(table_selector={"nested_table_policy": "flatten"})
    result = build_run(config, FIXED_NOW)
    trace = build_trace_payload(result)
    nested = _table(trace, "page_03_nested.html", 1)
    assert nested["decision"] == "accepted"
    assert nested["notes"] == ["nested_table_flattened"]
    assert nested["nested"] is True
    assert [column["name"] for column in nested["columns"]] == [
        "内层字段",
        "内层值",
    ]


def test_accepted_table_records_shape_evidence_and_columns(default_config):
    _, trace = _trace(default_config)
    table = _table(trace, "page_01.html", 0)
    assert table["decision"] == "accepted"
    assert table["filter_reason"] is None
    assert table["shape"] == {"column_count": 5, "row_count": 4}
    assert table["evidence"]["hash"]
    assert table["evidence"]["source_line_start"] >= 1
    assert (
        table["evidence"]["source_line_start"]
        <= table["evidence"]["source_line_end"]
    )
    assert [column["column_index"] for column in table["columns"]] == list(range(5))


def test_column_trace_records_hit_branch_ratio_and_samples(default_config):
    _, trace = _trace(default_config)
    table = _table(trace, "page_01.html", 0)
    name_column = table["columns"][0]
    inference = name_column["inference"]
    assert inference["branch"] == "identifier"
    assert inference["branch_kind"] == "priority"
    assert inference["priority_index"] == 5
    assert inference["match_ratio"] == 1.0
    assert inference["ratio_scope"] == "non_empty"
    assert inference["matched"] == 4
    assert inference["compared"] == 4
    assert [check["branch"] for check in inference["checks"]] == [
        branch for branch in INFERENCE_BRANCH_ORDER[:6]
    ]
    assert inference["checks"][-1]["passed"] is True
    assert name_column["samples"] == {
        "count": 4,
        "shown": 3,
        "truncated": True,
        "values": ["A-1001", "A-1002", "A-1003"],
    }


def test_column_trace_fallback_kinds_for_enum_and_text(default_config):
    _, trace = _trace(default_config)
    messy = _table(trace, "page_04_messy.html", 0)
    # 日期列命中 0.75 的比例阈值。
    date_column = messy["columns"][5]
    assert date_column["inference"]["branch"] == "date"
    assert date_column["inference"]["match_ratio"] == 0.75

    regions = _table(trace, "page_02.html", 0)["columns"][0]
    assert regions["inference"]["branch"] == "enum"
    assert regions["inference"]["branch_kind"] == "enum"
    assert regions["inference"]["priority_index"] is None

    indicator = _table(trace, "page_06_mixed.html", 1)["columns"][0]
    assert indicator["inference"]["branch"] == "text"
    assert indicator["inference"]["branch_kind"] == "fallback"


def test_sparse_and_empty_branches_are_distinguished(default_config):
    from schema_extractor.header_infer import _infer_column_with_trace

    profile, trace = _infer_column_with_trace(
        "备注", ["值", "", "", ""], False, default_config.inference,
        default_config.normalization, 0,
    )
    assert profile.type == "text"
    assert trace.branch == "text"
    assert trace.branch_kind == "sparse"
    assert trace.non_empty_ratio == 0.25
    assert trace.match_ratio == 0.25
    assert trace.ratio_scope == "total"
    assert trace.checks == []

    profile, trace = _infer_column_with_trace(
        "空列", ["", "  ", None], False, default_config.inference,
        default_config.normalization, 0,
    )
    assert profile.type == "text"
    assert trace.branch_kind == "empty"
    assert trace.match_ratio == 0.0
    assert trace.non_empty == 0
    assert trace.distinct_count == 0
    assert trace.samples_truncated is False


def test_header_th_and_hint_co_match_keeps_branch_order():
    """th 与 hint 同时命中时，th 分支优先，hint 仍记录为共同命中。"""
    from dataclasses import replace

    from schema_extractor.config import TableSelector
    from schema_extractor.dom import parse_html

    selector = TableSelector(
        min_rows=1,
        min_cols=2,
        require_header=True,
        header_hint_regex="^(?:编号|名称)",
        nested_table_policy="skip",
    )
    source = (
        "<table><tr><th>编号</th><th>名称</th></tr>"
        "<tr><td>A-1</td><td>甲</td></tr></table>"
    )
    candidate = extract_tables(parse_html(source), selector)[0]
    header = candidate.header_trace.to_dict()
    assert header["strategy"] == "first_row_th"
    assert header["co_matched"] == ["th", "hint_regex"]
    assert [signal["branch"] for signal in header["signals"]] == [
        "thead",
        "th",
        "hint_regex",
    ]
    assert header["signals"][1]["matched"] is True
    assert header["signals"][2]["matched"] is True


def test_hint_only_header_branch():
    from schema_extractor.config import TableSelector
    from schema_extractor.dom import parse_html

    selector = TableSelector(
        min_rows=1,
        min_cols=2,
        require_header=True,
        header_hint_regex="^(?:编号|名称)",
        nested_table_policy="skip",
    )
    source = (
        "<table><tr><td>编号</td><td>名称</td></tr>"
        "<tr><td>A-1</td><td>甲</td></tr></table>"
    )
    candidate = extract_tables(parse_html(source), selector)[0]
    assert candidate.header_trace.strategy == "first_row_hint"
    assert candidate.header_trace.co_matched == ["hint_regex"]
    assert candidate.header_trace.signals[1].matched is False
    assert candidate.header_trace.signals[2].matched is True


def test_normalization_failure_is_traced_but_not_double_counted(default_config):
    result, trace = _trace(default_config)
    assert trace["counts"]["normalization_failure_steps"] == 1
    assert trace["counts"]["issues"] == len(result.issues) == 1
    table = _table(trace, "page_04_messy.html", 0)
    steps = table["normalization"]["steps"]
    assert table["normalization"]["failures"] == 1
    assert steps == [
        {
            "row_index": 3,
            "column_index": 5,
            "col_name": "日期",
            "column_type": "date",
            "reason": "date_unparsable",
            "value": "2026-13-40",
        }
    ]
    # 追踪条目独立存在，但没有并入 issues。
    assert len(result.issues) == 1


def test_trace_is_new_report_field_and_old_fields_unchanged(default_config):
    result = build_run(default_config, FIXED_NOW)
    store = Store(default_config.database)
    store.initialize()
    payload = build_report(result, store, None)
    assert "trace" in payload
    assert payload["trace"] == build_trace_payload(result)
    # 既有字段保持原值。
    assert payload["tables_accepted"] == 5
    assert payload["tables_filtered_count"] == 3
    assert payload["issues_count"] == 1
    assert payload["records_written"] == 16
    assert payload["column_type_distribution"]["identifier"] == 2


def test_records_and_existing_summary_fields_are_unchanged(default_config):
    result, trace = _trace(default_config)
    # 追踪不改变 records 形状或数量。
    for record in result.all_records():
        assert set(record) == {
            "source_file",
            "table_index",
            "row_index",
            "values",
            "units",
        }
    assert result.records_written == 16


def test_trace_bytes_are_stable_across_repeated_runs(default_config):
    execute_run(default_config, now=FIXED_NOW)
    first = default_config.trace_json.read_bytes()
    execute_run(default_config, now=FIXED_NOW)
    second = default_config.trace_json.read_bytes()
    assert first == second
    assert first.endswith(b"\n")


def test_trace_json_matches_report_embedded_trace(default_config):
    result = execute_run(default_config, now=FIXED_NOW)
    embedded = json.loads(
        default_config.report_json.read_text(encoding="utf-8")
    )["trace"]
    standalone = json.loads(
        default_config.trace_json.read_text(encoding="utf-8")
    )
    assert embedded == standalone


def test_write_trace_creates_parent_directories(tmp_path):
    payload = {"version": 1, "files": []}
    path = tmp_path / "nested" / "trace.json"
    write_trace(payload, path)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_trace_uses_file_and_table_order(default_config):
    _, trace = _trace(default_config)
    assert [entry["source_file"] for entry in trace["files"]] == [
        "page_01.html",
        "page_02.html",
        "page_03_nested.html",
        "page_04_messy.html",
        "page_05_no_table.html",
        "page_06_mixed.html",
    ]
    indices = [
        table["table_index"]
        for entry in trace["files"]
        for table in entry["tables"]
    ]
    assert indices == [0, 0, 0, 1, 0, 0, 1, 2]


def test_zero_table_file_has_empty_tables_list(default_config):
    _, trace = _trace(default_config)
    page = trace["files"][4]
    assert page["source_file"] == "page_05_no_table.html"
    assert page["tables_detected"] == 0
    assert page["tables"] == []
    assert page["tables_accepted"] == 0


def test_trace_config_is_not_part_of_config_hash(default_config):
    # trace 文件路径是派生属性，不进入 config_sha256，因此 run_id 稳定。
    assert default_config.trace_json == default_config.report_json.with_name(
        "trace.json"
    )
    assert "trace_json" not in default_config.raw
