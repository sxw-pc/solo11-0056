"""表头语义与取值证据协同判定的测试。

覆盖三条确定规则：
1. 表头提示 + 取值比例不足但达到下限 -> 采信表头类型，低置信；
2. 表头与取值明显冲突 -> 回退 text 并产生列级 issue，不抛异常；
3. 表头提示几乎无取值支持 -> 忽略提示，维持取值判定（真整数不受影响）。
"""

import json
import sqlite3
from pathlib import Path

from schema_extractor.header_infer import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    HEADER_HINT_MIN_RATIO,
    TYPE_SOURCE_CONFLICT,
    TYPE_SOURCE_HEADER,
    TYPE_SOURCE_VALUE,
    infer_column,
)
from schema_extractor.pipeline import build_run, execute_run
from schema_extractor.processing import COLUMN_ISSUE_ROW, HEADER_VALUE_CONFLICT
from schema_extractor.report import build_report
from schema_extractor.storage import Store

FIXED_NOW = "2026-01-01T00:00:00Z"
ROOT = Path(__file__).resolve().parents[1]
LOW_CONFIDENCE_DIR = ROOT / "fixtures" / "inputs" / "low_confidence"
FIXTURE_NAME = "page_07_amount_header.html"


def _infer(name, values, config, unnamed=False):
    return infer_column(
        name,
        values,
        unnamed,
        config.inference,
        config.normalization,
    )


# --- 单元：表头采信路径 -------------------------------------------------


def test_amount_header_with_partial_symbol_evidence_adopts_currency(default_config):
    profile = _infer(
        "金额", ["1,200", "1,300", "800", "¥500"], default_config
    )
    assert profile.type == "currency"
    assert profile.type_source == TYPE_SOURCE_HEADER
    assert profile.confidence == CONFIDENCE_LOW
    assert profile.match_ratio == HEADER_HINT_MIN_RATIO == 0.25


def test_percent_header_with_partial_evidence_adopts_percent(default_config):
    profile = _infer("比例", ["10%", "20", "30", "40"], default_config)
    assert profile.type == "percent"
    assert profile.type_source == TYPE_SOURCE_HEADER
    assert profile.confidence == CONFIDENCE_LOW
    assert profile.match_ratio == 0.25


def test_amount_header_with_pure_numbers_stays_integer(default_config):
    """取值里没有任何货币符号：提示得不到支持，真整数维持 integer。"""
    profile = _infer(
        "金额", ["1,200", "1,300", "800", "900"], default_config
    )
    assert profile.type == "integer"
    assert profile.type_source == TYPE_SOURCE_VALUE
    assert profile.confidence == CONFIDENCE_HIGH
    assert profile.match_ratio == 1.0


def test_hint_below_floor_falls_back_to_value_type(default_config):
    """5 行只有 1 行带符号：0.2 低于下限 0.25，不采信表头。"""
    profile = _infer(
        "金额", ["¥1", "10", "20", "30", "40"], default_config
    )
    assert profile.type == "integer"
    assert profile.type_source == TYPE_SOURCE_VALUE
    assert profile.confidence == CONFIDENCE_HIGH
    assert profile.match_ratio == 0.8


def test_identifier_header_agreement_is_high_confidence(default_config):
    profile = _infer("编号", ["A-01", "A-02", "B-03"], default_config)
    assert profile.type == "identifier"
    assert profile.type_source == TYPE_SOURCE_VALUE
    assert profile.confidence == CONFIDENCE_HIGH


def test_column_without_hint_uses_values_only(default_config):
    profile = _infer("备注", ["¥1", "¥2", "¥3"], default_config)
    assert profile.type == "currency"
    assert profile.type_source == TYPE_SOURCE_VALUE
    assert profile.confidence == CONFIDENCE_HIGH


def test_unnamed_column_never_uses_header_hint(default_config):
    profile = _infer(
        "col_2", ["1,200", "1,300", "800", "¥500"], default_config, unnamed=True
    )
    assert profile.type == "integer"
    assert profile.type_source == TYPE_SOURCE_VALUE
    assert profile.confidence == CONFIDENCE_HIGH


# --- 单元：冲突回退路径 -------------------------------------------------


def test_date_header_conflicting_with_currency_values_becomes_text(default_config):
    profile = _infer(
        "日期", ["¥10", "¥20", "¥30", "2026-01-05"], default_config
    )
    assert profile.type == "text"
    assert profile.type_source == TYPE_SOURCE_CONFLICT
    assert profile.confidence == CONFIDENCE_LOW
    assert profile.match_ratio == 1.0


def test_hint_priority_order_is_deterministic(default_config):
    """同时命中多个提示词时按固定类型顺序裁决：日期先于货币。"""
    profile = _infer(
        "日期金额", ["¥1", "¥2", "¥3", "2026-01-01"], default_config
    )
    assert profile.type == "text"
    assert profile.type_source == TYPE_SOURCE_CONFLICT
    assert profile.confidence == CONFIDENCE_LOW


def test_low_confidence_dict_carries_marker_keys(default_config):
    profile = _infer("金额", ["1,200", "1,300", "800", "¥500"], default_config)
    payload = profile.to_dict()
    assert payload["type_source"] == TYPE_SOURCE_HEADER
    assert payload["confidence"] == CONFIDENCE_LOW


def test_high_confidence_dict_has_no_marker_keys(default_config):
    profile = _infer("金额", ["1,200", "1,300", "800", "900"], default_config)
    payload = profile.to_dict()
    assert set(payload) == {
        "name",
        "type",
        "match_ratio",
        "sample_values",
        "unnamed",
    }


# --- 集成：新 fixture 全链路 -------------------------------------------


def _low_confidence_config(config_factory):
    return config_factory(input_dir=str(LOW_CONFIDENCE_DIR))


def test_fixture_columns_carry_expected_markers(config_factory):
    config = _low_confidence_config(config_factory)
    result = build_run(config, FIXED_NOW)
    assert result.files_scanned == 1
    assert len(result.accepted_tables) == 1
    table = result.accepted_tables[0]
    profiles = {column.name: column for column in table.columns}

    identifier = profiles["编号"]
    assert identifier.type == "identifier"
    assert identifier.type_source == TYPE_SOURCE_VALUE
    assert identifier.confidence == CONFIDENCE_HIGH

    amount = profiles["金额"]
    assert amount.type == "currency"
    assert amount.type_source == TYPE_SOURCE_HEADER
    assert amount.confidence == CONFIDENCE_LOW
    assert amount.match_ratio == 0.25

    conflicted = profiles["日期"]
    assert conflicted.type == "text"
    assert conflicted.type_source == TYPE_SOURCE_CONFLICT
    assert conflicted.confidence == CONFIDENCE_LOW


def test_fixture_records_normalize_amounts_and_keep_known_units(config_factory):
    config = _low_confidence_config(config_factory)
    result = build_run(config, FIXED_NOW)
    records = result.records_for(FIXTURE_NAME)
    assert len(records) == 4
    for record in records:
        assert set(record) == {
            "source_file",
            "table_index",
            "row_index",
            "values",
            "units",
        }
    # 无符号数字按金额规范化但不标注单位。
    assert records[0]["values"]["金额"] == "1200"
    assert "金额" not in records[0]["units"]
    # 带符号的行保留单位。
    assert records[3]["values"]["金额"] == "500"
    assert records[3]["units"]["金额"] == "CNY"
    # 冲突列回退 text：取值原样保留，不产生行级 issue。
    assert records[0]["values"]["日期"] == "¥10"
    assert records[3]["values"]["日期"] == "2026-01-05"


def test_conflict_produces_column_level_issue(config_factory):
    config = _low_confidence_config(config_factory)
    result = build_run(config, FIXED_NOW)
    assert result.issues == [
        {
            "source_file": FIXTURE_NAME,
            "table_index": 0,
            "row_index": COLUMN_ISSUE_ROW,
            "col_name": "日期",
            "reason": HEADER_VALUE_CONFLICT,
        }
    ]


def test_execute_run_persists_markers_to_sqlite(config_factory):
    config = _low_confidence_config(config_factory)
    result = execute_run(config, now=FIXED_NOW)
    store = Store(config.database)
    columns = {
        row["name"]: row for row in store.columns_for_run(result.run_id)
    }
    assert (columns["编号"]["type_source"], columns["编号"]["confidence"]) == (
        "value",
        "high",
    )
    assert (columns["金额"]["type_source"], columns["金额"]["confidence"]) == (
        "header",
        "low",
    )
    assert (columns["日期"]["type_source"], columns["日期"]["confidence"]) == (
        "conflict_fallback",
        "low",
    )
    issues = store.issues_for_run(result.run_id)
    assert [issue["reason"] for issue in issues] == [HEADER_VALUE_CONFLICT]
    assert issues[0]["row_index"] == COLUMN_ISSUE_ROW


def test_report_marks_low_confidence_columns(config_factory):
    config = _low_confidence_config(config_factory)
    result = execute_run(config, now=FIXED_NOW)
    payload = json.loads(config.report_json.read_text(encoding="utf-8"))
    assert payload["run_id"] == result.run_id
    low = payload["low_confidence_columns"]
    assert [(item["name"], item["type_source"]) for item in low] == [
        ("金额", "header"),
        ("日期", "conflict_fallback"),
    ]
    assert all(item["confidence"] == "low" for item in low)
    assert payload["issues_top"] == [
        {"reason": HEADER_VALUE_CONFLICT, "count": 1}
    ]
    markdown = config.report_md.read_text(encoding="utf-8")
    assert "## 低置信列" in markdown
    assert "| 低置信列数 | 2 |" in markdown
    assert "conflict_fallback" in markdown


def test_low_confidence_run_is_deterministic(config_factory):
    config = _low_confidence_config(config_factory)
    store = Store(config.database)
    store.initialize()
    first = build_report(build_run(config, FIXED_NOW), store, None)
    second = build_report(build_run(config, FIXED_NOW), store, None)
    assert first == second


def test_sqlite_migration_adds_marker_columns_to_old_database(tmp_path):
    """旧版 columns 表（无标记列）在 initialize 后应补齐新列。"""
    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE columns (
                run_id TEXT NOT NULL,
                source_file TEXT NOT NULL,
                table_index INTEGER NOT NULL,
                col_index INTEGER NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                match_ratio REAL NOT NULL,
                PRIMARY KEY (run_id, source_file, table_index, col_index)
            );
            """
        )
    store = Store(database)
    store.initialize()
    with sqlite3.connect(database) as connection:
        names = {
            row[1] for row in connection.execute("PRAGMA table_info(columns)")
        }
    assert {"type_source", "confidence"} <= names
    # 重复 initialize 幂等，不抛异常。
    store.initialize()
