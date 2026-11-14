import json

from schema_extractor.header_infer import ColumnProfile
from schema_extractor.models import RunResult, TableResult
from schema_extractor.pipeline import build_run
from schema_extractor.processing import snapshot_rows
from schema_extractor.report import (
    build_diff,
    build_report,
    render_markdown,
    summary_for_golden,
    write_reports,
)
from schema_extractor.storage import Store

FIXED_NOW = "2026-01-01T00:00:00Z"


def _column(name: str, type_name: str) -> ColumnProfile:
    return ColumnProfile(
        name=name,
        type=type_name,
        match_ratio=1.0,
        sample_values=[],
        unnamed=False,
    )


def _table(source_file, table_index, columns, row_count):
    return TableResult(
        source_file=source_file,
        table_index=table_index,
        caption="",
        columns=[_column(name, type_name) for name, type_name in columns],
        records=[{"row_index": index} for index in range(row_count)],
        issues=[],
        evidence_hash="hash",
        source_line_start=1,
        source_line_end=1,
        nested=False,
    )


def _result(tables, run_id="current-run"):
    return RunResult(
        run_id=run_id,
        started_at=FIXED_NOW,
        finished_at=FIXED_NOW,
        config_sha256="sha",
        files_scanned=1,
        tables_detected=len(tables),
        accepted_tables=tables,
    )


def _save_baseline(store, tables_spec, run_id="baseline-run"):
    """tables_spec: (source_file, table_index, [(name, type), ...], row_count) 列表。"""
    run = {
        "run_id": run_id,
        "started_at": FIXED_NOW,
        "finished_at": FIXED_NOW,
        "config_sha256": "sha",
        "files_scanned": 1,
        "records_written": sum(spec[3] for spec in tables_spec),
        "issues_count": 0,
    }
    tables = []
    columns = []
    for source_file, table_index, cols, row_count in tables_spec:
        tables.append(
            {
                "run_id": run_id,
                "source_file": source_file,
                "table_index": table_index,
                "evidence_hash": "hash",
                "column_count": len(cols),
                "row_count": row_count,
            }
        )
        for col_index, (name, type_name) in enumerate(cols):
            columns.append(
                {
                    "run_id": run_id,
                    "source_file": source_file,
                    "table_index": table_index,
                    "col_index": col_index,
                    "name": name,
                    "type": type_name,
                    "match_ratio": 1.0,
                }
            )
    store.save_snapshot(run, tables, columns, [], [])
    return run_id


def _payload(default_config):
    result = build_run(default_config, FIXED_NOW)
    store = Store(default_config.database)
    store.initialize()
    return result, store, build_report(result, store, None)


def test_report_overview_fields(default_config):
    result, _, payload = _payload(default_config)
    assert payload["run_id"] == result.run_id
    assert payload["generated_at"] == FIXED_NOW
    assert payload["files_scanned"] == 6
    assert payload["tables_detected"] == 8
    assert payload["tables_accepted"] == 5
    assert payload["tables_filtered_count"] == 3
    assert payload["records_written"] == 16
    assert payload["issues_count"] == 1
    assert payload["issues_top"] == [{"reason": "date_unparsable", "count": 1}]


def test_report_type_distribution_and_coverage(default_config):
    _, _, payload = _payload(default_config)
    assert payload["column_type_distribution"]["identifier"] == 2
    assert payload["column_type_distribution"]["date"] == 3
    assert payload["evidence_coverage"] == {
        "tables_with_hash": 5,
        "tables_total": 5,
        "ratio": 1.0,
    }


def test_report_contains_filtered_reasons_and_tables(default_config):
    _, _, payload = _payload(default_config)
    reasons = {item["reason"] for item in payload["tables_filtered"]}
    assert reasons == {
        "nested_table_skipped",
        "below_min_columns",
        "missing_header",
    }
    assert len(payload["tables"]) == 5
    assert payload["tables"][0]["column_count"] == 5


def test_diff_without_baseline_is_explicit(default_config):
    result = build_run(default_config, FIXED_NOW)
    store = Store(default_config.database)
    store.initialize()
    diff = build_diff(result, store, None)
    assert diff == {
        "status": "no_baseline",
        "baseline_run_id": None,
        "message": "无基线",
        "records_delta": None,
        "new_tables": [],
        "disappeared_tables": [],
        "current_records": 16,
    }


def test_diff_with_saved_baseline(default_config):
    result = build_run(default_config, FIXED_NOW)
    store = Store(default_config.database)
    store.initialize()
    tables, columns, records, issues = snapshot_rows(result)
    store.save_snapshot(
        {
            "run_id": result.run_id,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "config_sha256": result.config_sha256,
            "files_scanned": result.files_scanned,
            "records_written": result.records_written,
            "issues_count": len(result.issues),
        },
        tables,
        columns,
        records,
        issues,
    )
    diff = build_diff(result, store, result.run_id)
    assert diff["status"] == "compared"
    assert diff["records_delta"] == 0
    assert diff["baseline_records"] == diff["current_records"] == 16
    assert len(diff["table_diffs"]) == 5
    assert all(
        entry["rows_delta"] == 0
        and entry["type_changed"] == []
        and entry["new_columns"] == []
        for entry in diff["table_diffs"]
    )


def test_diff_table_level_type_change_new_column_and_row_delta(default_config):
    store = Store(default_config.database)
    store.initialize()
    baseline_id = _save_baseline(
        store,
        [("page.html", 0, [("编号", "identifier"), ("金额", "currency")], 2)],
    )
    result = _result(
        [
            _table(
                "page.html",
                0,
                [("编号", "identifier"), ("金额", "number"), ("备注", "text")],
                3,
            )
        ]
    )
    diff = build_diff(result, store, baseline_id)
    assert diff["status"] == "compared"
    assert diff["records_delta"] == 1
    assert diff["table_diffs"] == [
        {
            "table": "page.html#0",
            "baseline_rows": 2,
            "current_rows": 3,
            "rows_delta": 1,
            "type_changed": [
                {
                    "column": "金额",
                    "baseline_type": "currency",
                    "current_type": "number",
                }
            ],
            "new_columns": ["备注"],
        }
    ]


def test_diff_column_order_swap_is_not_type_change(default_config):
    store = Store(default_config.database)
    store.initialize()
    baseline_id = _save_baseline(
        store, [("page.html", 0, [("日期", "date"), ("名称", "text")], 2)]
    )
    result = _result(
        [_table("page.html", 0, [("名称", "text"), ("日期", "date")], 2)]
    )
    diff = build_diff(result, store, baseline_id)
    entry = diff["table_diffs"][0]
    assert entry["rows_delta"] == 0
    assert entry["type_changed"] == []
    assert entry["new_columns"] == []


def test_diff_disjoint_column_sets_never_produce_type_changes(default_config):
    store = Store(default_config.database)
    store.initialize()
    baseline_id = _save_baseline(
        store, [("page.html", 0, [("旧列", "date")], 2)]
    )
    result = _result([_table("page.html", 0, [("新列", "text")], 2)])
    diff = build_diff(result, store, baseline_id)
    entry = diff["table_diffs"][0]
    assert entry["type_changed"] == []
    assert entry["new_columns"] == ["新列"]


def test_diff_disappeared_table_stays_out_of_table_diffs(default_config):
    store = Store(default_config.database)
    store.initialize()
    baseline_id = _save_baseline(
        store,
        [
            ("gone.html", 0, [("编号", "identifier")], 1),
            ("page.html", 0, [("编号", "identifier")], 2),
        ],
    )
    result = _result([_table("page.html", 0, [("编号", "identifier")], 2)])
    diff = build_diff(result, store, baseline_id)
    assert diff["disappeared_tables"] == ["gone.html#0"]
    assert [entry["table"] for entry in diff["table_diffs"]] == ["page.html#0"]
    assert diff["table_diffs"][0]["type_changed"] == []


def test_diff_table_diffs_sorted_by_table_key_and_column_name(default_config):
    store = Store(default_config.database)
    store.initialize()
    baseline_id = _save_baseline(
        store,
        [
            ("b.html", 0, [("x", "date"), ("a", "text")], 1),
            ("a.html", 0, [("m", "number")], 1),
        ],
    )
    result = _result(
        [
            _table("b.html", 0, [("x", "text"), ("a", "date")], 1),
            _table("a.html", 0, [("m", "text")], 1),
        ]
    )
    diff = build_diff(result, store, baseline_id)
    assert [entry["table"] for entry in diff["table_diffs"]] == [
        "a.html#0",
        "b.html#0",
    ]
    changes = diff["table_diffs"][1]["type_changed"]
    assert [change["column"] for change in changes] == ["a", "x"]


def test_markdown_renders_table_level_diff(default_config):
    store = Store(default_config.database)
    store.initialize()
    baseline_id = _save_baseline(
        store, [("page.html", 0, [("金额", "currency")], 2)]
    )
    result = _result(
        [_table("page.html", 0, [("金额", "number"), ("备注", "text")], 3)]
    )
    payload = build_report(result, store, baseline_id)
    markdown = render_markdown(payload)
    assert "表级差异" in markdown
    assert "`page.html#0`" in markdown
    assert "行数 +1（2 → 3）" in markdown
    assert "`金额` currency→number" in markdown
    assert "新增列：`备注`" in markdown


def test_markdown_has_all_required_sections(default_config):
    _, _, payload = _payload(default_config)
    markdown = render_markdown(payload)
    for heading in (
        "# 标程抽取台抽取质量报告",
        "## 概览",
        "## 采信表格",
        "## 列类型分布",
        "## Issues Top",
        "## 证据覆盖",
        "## 被过滤表格",
        "## 与上一次 run 差异",
        "无基线",
    ):
        assert heading in markdown


def test_write_reports_creates_stable_json(default_config, tmp_path):
    _, _, payload = _payload(default_config)
    md = tmp_path / "report.md"
    js = tmp_path / "report.json"
    write_reports(payload, md, js)
    assert md.is_file()
    loaded = json.loads(js.read_text(encoding="utf-8"))
    assert loaded == payload
    assert js.read_text(encoding="utf-8").endswith("\n")


def test_golden_summary_excludes_volatile_fields(default_config):
    result, _, _ = _payload(default_config)
    summary = summary_for_golden(result)
    assert "run_id" not in summary
    assert "generated_at" not in summary
    assert summary["records_written"] == 16
    assert summary["files"][0]["source_file"] == "page_01.html"
    assert summary["tables"][0]["columns"][0]["name"] == "项目编号"


def test_report_uses_no_relative_output_paths(default_config):
    _, _, payload = _payload(default_config)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "build/default" not in serialized
    assert ":\\" not in serialized
