import json

import pytest

from schema_extractor.pipeline import (
    PipelineError,
    build_run,
    canonical_now,
    execute_run,
    records_jsonl,
)
from schema_extractor.storage import Store

FIXED_NOW = "2026-01-01T00:00:00Z"


def test_build_run_summary_and_records(default_config):
    result = build_run(default_config, FIXED_NOW)
    assert result.files_scanned == 6
    assert result.tables_detected == 8
    assert len(result.accepted_tables) == 5
    assert len(result.filtered_tables) == 3
    assert result.records_written == 16
    assert len(result.issues) == 1
    assert result.issues[0]["reason"] == "date_unparsable"
    assert result.run_id
    assert len(result.run_id) == 16
    assert result.started_at == FIXED_NOW
    assert result.finished_at == FIXED_NOW


def test_records_are_normalized_and_units_are_explicit(default_config):
    result = build_run(default_config, FIXED_NOW)
    first = result.records_for("page_01.html")[0]
    assert first["values"]["合同金额"] == "1234.5"
    assert first["units"]["合同金额"] == "CNY"
    assert first["values"]["完成率"] == "12.5"
    assert first["units"]["完成率"] == "%"
    assert first["values"]["更新日期"] == "2026-01-02"


def test_execute_run_writes_all_outputs(default_config):
    result = execute_run(default_config, now=FIXED_NOW)
    assert default_config.database.is_file()
    assert default_config.report_md.is_file()
    assert default_config.report_json.is_file()
    assert default_config.trace_json.is_file()
    assert default_config.evidence_index.is_file()
    evidence_files = list(default_config.evidence_dir.glob("*.html"))
    assert len(evidence_files) == 5
    payload = json.loads(default_config.report_json.read_text(encoding="utf-8"))
    assert payload["run_id"] == result.run_id
    assert payload["tables_accepted"] == 5
    assert payload["diff"]["status"] == "no_baseline"
    assert payload["diff"]["message"] == "无基线"


def test_same_now_produces_identical_report_bytes(default_config):
    execute_run(default_config, now=FIXED_NOW)
    first = default_config.report_json.read_bytes()
    execute_run(default_config, now=FIXED_NOW)
    second = default_config.report_json.read_bytes()
    assert first == second


def test_same_now_produces_identical_trace_bytes(default_config):
    execute_run(default_config, now=FIXED_NOW)
    first = default_config.trace_json.read_bytes()
    execute_run(default_config, now=FIXED_NOW)
    second = default_config.trace_json.read_bytes()
    assert first == second
    assert first.endswith(b"\n")
    # 追踪同时写入 report.json，且与独立文件一致。
    embedded = json.loads(
        default_config.report_json.read_text(encoding="utf-8")
    )["trace"]
    assert embedded == json.loads(first)


def test_different_now_changes_run_id_but_not_records(default_config):
    first = execute_run(default_config, now=FIXED_NOW)
    second = execute_run(default_config, now="2026-01-02T00:00:00Z")
    assert first.run_id != second.run_id
    assert first.records_written == second.records_written
    assert records_jsonl(first.all_records()) == records_jsonl(second.all_records())


def test_diff_with_existing_baseline(default_config):
    baseline = execute_run(default_config, now=FIXED_NOW)
    current = execute_run(
        default_config,
        now="2026-01-02T00:00:00Z",
        diff_with=baseline.run_id,
    )
    diff = current.report["diff"]
    assert diff["status"] == "compared"
    assert diff["records_delta"] == 0
    assert diff["new_tables"] == []
    assert diff["disappeared_tables"] == []


def test_diff_with_missing_baseline_is_explicit(default_config):
    result = execute_run(default_config, now=FIXED_NOW, diff_with="not-found")
    assert result.report["diff"]["status"] == "no_baseline"
    assert result.report["diff"]["message"] == "无基线"


def test_evidence_index_maps_hash_to_source_location(default_config):
    result = build_run(default_config, FIXED_NOW)
    assert len(result.evidence_index) == 5
    for table in result.accepted_tables:
        entry = result.evidence_index[table.evidence_hash]
        assert entry["source_file"] == table.source_file
        assert entry["start"] == table.source_line_start
        assert entry["end"] == table.source_line_end


def test_sqlite_snapshot_matches_result(default_config):
    result = execute_run(default_config, now=FIXED_NOW)
    store = Store(default_config.database)
    assert store.get_run(result.run_id)["records_written"] == 16
    assert store.count_records(result.run_id) == 16
    assert len(store.tables_for_run(result.run_id)) == 5


def test_issue_is_persisted(default_config):
    result = execute_run(default_config, now=FIXED_NOW)
    store = Store(default_config.database)
    issues = store.issues_for_run(result.run_id, limit=20)
    assert issues == [
        {
            "source_file": "page_04_messy.html",
            "table_index": 0,
            "row_index": 3,
            "col_name": "日期",
            "reason": "date_unparsable",
        }
    ]


def test_records_jsonl_is_one_compact_object_per_line(default_config):
    result = build_run(default_config, FIXED_NOW)
    text = records_jsonl(result.all_records())
    lines = text.strip().splitlines()
    assert len(lines) == 16
    assert json.loads(lines[0])["source_file"] == "page_01.html"


def test_missing_input_directory_raises_pipeline_error(config_factory, tmp_path):
    config = config_factory(input_dir=str(tmp_path / "missing"))
    with pytest.raises(PipelineError, match="输入目录不存在"):
        build_run(config, FIXED_NOW)


def test_empty_input_directory_is_valid(config_factory, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    config = config_factory(input_dir=str(empty))
    result = build_run(config, FIXED_NOW)
    assert result.files_scanned == 0
    assert result.tables_detected == 0
    assert result.records_written == 0


def test_canonical_now_accepts_offsets_and_naive_values():
    assert canonical_now("2026-01-01T08:00:00+08:00") == "2026-01-01T00:00:00Z"
    assert canonical_now("2026-01-01T00:00:00") == "2026-01-01T00:00:00Z"
    with pytest.raises(PipelineError):
        canonical_now("not-a-time")


def test_file_glob_limits_the_input_set(config_factory):
    config = config_factory(file_glob="page_01.html")
    result = build_run(config, FIXED_NOW)
    assert result.files_scanned == 1
    assert result.tables_detected == 1
    assert result.records_written == 4
    assert result.records_for("page_01.html")
