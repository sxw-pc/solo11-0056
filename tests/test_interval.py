"""区间值：拆解、单位冲突 issue、编号不被误拆，以及列类型探测。"""

import json
from decimal import Decimal

import yaml

from schema_extractor.config import load_config
from schema_extractor.header_infer import TYPE_ORDER, infer_column
from schema_extractor.models import cell_value
from schema_extractor.normalize import (
    INTERVAL_UNIT_CONFLICT,
    INTERVAL_UNPARSABLE,
    analyze_interval,
    is_interval,
    normalize_cell,
    parse_interval,
)
from schema_extractor.pipeline import execute_run
from schema_extractor.storage import Store


def _infer(name, values, config):
    return infer_column(
        name, values, False, config.inference, config.normalization
    )


# ---------------------------------------------------------------------------
# 区间成功拆解
# ---------------------------------------------------------------------------


def test_plain_dash_interval_without_unit():
    value = parse_interval("100-200")
    assert value is not None
    assert (value.lower, value.upper, value.unit) == (100, 200, "")


def test_tilde_interval_with_currency_on_both_ends():
    normalized, unit, reason = normalize_cell("¥300~¥500", "interval")
    assert unit == "CNY"
    assert reason is None
    assert cell_value(normalized) == {
        "kind": "interval",
        "lower": "300",
        "upper": "500",
    }


def test_percent_interval_both_ends_share_percent_unit():
    normalized, unit, reason = normalize_cell("5%~8%", "interval")
    assert unit == "%"
    assert reason is None
    assert cell_value(normalized) == {
        "kind": "interval",
        "lower": "5",
        "upper": "8",
    }


def test_interval_supports_decimal_and_thousands_separators():
    value = parse_interval("￥1,000.50—￥2,000")
    assert value is not None
    assert (value.lower, value.upper, value.unit) == (
        Decimal("1000.50"),
        Decimal("2000"),
        "CNY",
    )


def test_interval_boundaries_are_canonical_decimals():
    normalized, _, _ = normalize_cell("1.50 ~ 2.500", "interval")
    assert cell_value(normalized) == {
        "kind": "interval",
        "lower": "1.5",
        "upper": "2.5",
    }


def test_interval_type_is_in_type_order():
    assert "interval" in TYPE_ORDER
    assert TYPE_ORDER.index("interval") < TYPE_ORDER.index("text")


# ---------------------------------------------------------------------------
# 单位冲突 issue（失败路径不抛异常）
# ---------------------------------------------------------------------------


def test_one_sided_unit_is_reported_not_raised():
    # 一端带单位、另一端不带：无法确定整段区间的单位。
    for cell in ("100-300元", "¥10~20", "5%~8"):
        normalized, unit, reason = normalize_cell(cell, "interval")
        assert normalized == cell
        assert unit == ""
        assert reason == INTERVAL_UNIT_CONFLICT


def test_mismatched_currency_units_are_reported():
    normalized, unit, reason = normalize_cell("¥100~$200", "interval")
    assert (normalized, unit, reason) == ("¥100~$200", "", INTERVAL_UNIT_CONFLICT)


def test_reversed_bounds_are_reported():
    normalized, _, reason = normalize_cell("200-100", "interval")
    assert normalized == "200-100"
    assert reason == "interval_bounds_reversed"


def test_non_numeric_cell_keeps_cleaned_text_with_issue():
    normalized, unit, reason = normalize_cell("  面议  ", "interval")
    assert (normalized, unit, reason) == ("面议", "", INTERVAL_UNPARSABLE)


def test_interval_cells_never_raise_on_garbage():
    for cell in ("", "~", "-", "100-", "-200", "abc~def", None):
        # 只断言不抛异常且失败时返回 reason 或空串。
        normalized, _unit, _reason = normalize_cell(cell, "interval")
        assert isinstance(normalized, str)


# ---------------------------------------------------------------------------
# 编号不被误拆
# ---------------------------------------------------------------------------


def test_identifier_is_not_interval():
    for cell in ("A-1001", "B-2002", "P-2026-01", "ID_1001"):
        assert analyze_interval(cell).shaped is False
        assert is_interval(cell) is False


def test_identifier_column_stays_identifier(default_config):
    profile = _infer("项目编号", ["A-1001", "A-1002", "A-1003"], default_config)
    assert profile.type == "identifier"


def test_date_is_not_interval():
    assert is_interval("2026-01-02") is False


# ---------------------------------------------------------------------------
# 列类型探测与端到端记录
# ---------------------------------------------------------------------------


def test_interval_column_is_detected_not_text(default_config):
    profile = _infer(
        "金额区间", ["100-200", "¥300~¥500", "10.5~20"], default_config
    )
    assert profile.type == "interval"
    assert profile.match_ratio == 1.0


def test_interval_column_stays_interval_with_unit_conflict(default_config):
    # 有一行单位冲突也要继续认成区间列，由规范化给出 issue，而不是回退 text。
    profile = _infer(
        "金额区间", ["100-200", "100-300元", "10.5~20", "面议"], default_config
    )
    assert profile.type == "interval"
    assert profile.match_ratio == 0.75


def test_interval_and_identifier_columns_coexist(default_config):
    profiles = [
        _infer("项目编号", ["A-1001", "A-1002"], default_config),
        _infer("金额区间", ["100-200", "¥300~¥500"], default_config),
    ]
    assert [profile.type for profile in profiles] == ["identifier", "interval"]


def _run_pipeline(tmp_path, rows: list[tuple[str, str, str]]):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    body = "".join(
        f"<tr><td>{code}</td><td>{amount}</td><td>{ratio}</td></tr>"
        for code, amount, ratio in rows
    )
    (inputs / "ranges.html").write_text(
        "<!doctype html><html><head><meta charset=\"utf-8\"></head><body>"
        "<table><thead><tr><th>项目编号</th><th>金额区间</th><th>比例区间</th>"
        f"</tr></thead><tbody>{body}</tbody></table></body></html>",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "input_dir": str(inputs),
                "output_dir": str(out),
                "database": str(out / "runs.sqlite3"),
                "evidence_dir": str(out / "evidence"),
                "evidence_index": str(out / "evidence.json"),
                "report_md": str(out / "report.md"),
                "report_json": str(out / "report.json"),
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    return execute_run(config, now="2026-01-01T00:00:00Z"), config


def test_pipeline_writes_interval_values_and_units_end_to_end(tmp_path):
    result, config = _run_pipeline(
        tmp_path,
        [
            ("A-1001", "100-200", "5%~8%"),
            ("A-1002", "¥300~¥500", "3% ~ 6%"),
        ],
    )
    table = result.accepted_tables[0]
    assert [column.type for column in table.columns] == [
        "identifier",
        "interval",
        "interval",
    ]
    first = table.records[0]
    assert first["values"]["项目编号"] == "A-1001"
    assert first["values"]["金额区间"] == {
        "kind": "interval",
        "lower": "100",
        "upper": "200",
    }
    assert first["units"]["比例区间"] == "%"
    assert table.records[1]["units"]["金额区间"] == "CNY"

    # SQLite 中 values 仍以 JSON 保存，区间是嵌套对象，单值仍是扁平字符串。
    store = Store(config.database)
    with store.connect() as connection:
        payload = json.loads(
            connection.execute(
                "SELECT data_json FROM records WHERE row_index=0"
            ).fetchone()[0]
        )
    assert payload["values"]["项目编号"] == "A-1001"
    assert payload["values"]["金额区间"]["kind"] == "interval"


def test_pipeline_reports_issue_for_non_interval_cell_and_keeps_text(tmp_path):
    result, _ = _run_pipeline(
        tmp_path,
        [
            ("A-1001", "100-200", "5%~8%"),
            ("A-1002", "100-300元", "面议"),
            ("A-1003", "¥300~¥500", "3%~6%"),
            ("A-1004", "20~30", "1% ~ 2%"),
        ],
    )
    table = result.accepted_tables[0]
    # 75% 的区间形状达到阈值：冲突行与非数值行不把整列打回 text。
    assert [column.type for column in table.columns] == [
        "identifier",
        "interval",
        "interval",
    ]
    assert table.records[1]["values"]["金额区间"] == "100-300元"
    assert table.records[1]["values"]["比例区间"] == "面议"
    reasons = {
        (issue["row_index"], issue["col_name"]): issue["reason"]
        for issue in result.issues
    }
    assert reasons[(1, "金额区间")] == INTERVAL_UNIT_CONFLICT
    assert reasons[(1, "比例区间")] == INTERVAL_UNPARSABLE


def test_interval_columns_do_not_break_scalar_golden_fixtures(extracted):
    # 既有 fixture 没有区间列，页面记录形状必须与以前一致（golden 另行逐行断言）。
    for table in extracted.accepted_tables:
        assert all(column.type != "interval" for column in table.columns)
        for record in table.records:
            assert all(isinstance(value, str) for value in record["values"].values())
