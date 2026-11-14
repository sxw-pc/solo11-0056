import json

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
