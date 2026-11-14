"""列覆盖规则：命中、改名、强制类型、冲突消解与失败路径。"""

import json
from pathlib import Path

import pytest
import yaml

from schema_extractor.config import ColumnOverride, ConfigError, load_config
from schema_extractor.header_infer import select_column_override
from schema_extractor.pipeline import build_run, execute_run, override_warnings
from schema_extractor.report import build_report, render_markdown, summary_for_golden
from schema_extractor.storage import Store

FIXED_NOW = "2026-01-01T00:00:00Z"
PAGE_04 = "page_04_messy.html"
ROOT = Path(__file__).resolve().parents[1]


def _table(result, source_file, table_index=0):
    for table in result.accepted_tables:
        if table.source_file == source_file and table.table_index == table_index:
            return table
    raise AssertionError(f"未找到表 {source_file}#{table_index}")


def _columns(table):
    return {column.name: column for column in table.columns}


def _write_config(tmp_path, data):
    config_path = tmp_path / "override.yaml"
    config_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return config_path


def _base_config_data(tmp_path, **extra):
    output = tmp_path / "out"
    data = {
        "input_dir": str(ROOT / "fixtures" / "inputs"),
        "output_dir": str(output),
        "database": str(output / "runs.sqlite3"),
        "evidence_dir": str(output / "evidence"),
        "evidence_index": str(output / "evidence.json"),
        "report_md": str(output / "report.md"),
        "report_json": str(output / "report.json"),
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# 配置层
# ---------------------------------------------------------------------------


def test_config_without_overrides_has_empty_tuple(default_config):
    assert default_config.column_overrides == ()


def test_config_parses_overrides_in_order(config_factory):
    config = config_factory(
        column_overrides=[
            {"column_index": 2, "name": "备注"},
            {
                "source_glob": "page_04_*.html",
                "column_name_regex": "^金额",
                "type": "currency",
                "priority": 5,
            },
        ]
    )
    assert len(config.column_overrides) == 2
    first, second = config.column_overrides
    assert (first.source_glob, first.column_index, first.name) == ("*", 2, "备注")
    assert first.type is None and first.priority == 0 and first.order == 0
    assert second.order == 1
    assert second.priority == 5
    assert second.column_name_regex == "^金额"
    assert second.name is None


def test_config_defaults_source_glob_to_star(config_factory):
    config = config_factory(column_overrides=[{"column_index": 0, "type": "text"}])
    assert config.column_overrides[0].source_glob == "*"


@pytest.mark.parametrize(
    "rule, message",
    [
        ({"column_index": 0}, "必须至少提供 name 或 type"),
        ({"name": "x"}, "必须至少提供 column_name_regex 或 column_index"),
        (
            {"column_index": 0, "type": "money"},
            "type 只能是",
        ),
        ({"column_index": -1, "name": "x"}, "column_index 必须是 >= 0 的整数"),
        ({"column_index": "0", "name": "x"}, "column_index 必须是 >= 0 的整数"),
        ({"column_name_regex": "[", "name": "x"}, "column_name_regex 非法"),
        ({"column_name_regex": "", "name": "x"}, "column_name_regex 必须是非空字符串"),
        ({"column_index": 0, "name": ""}, "name 必须是非空字符串"),
        ({"column_index": 0, "name": "x", "priority": True}, "priority 必须是整数"),
        ({"column_index": 0, "name": "x", "unknown": 1}, "存在未知配置项"),
        ({"column_index": 0, "name": "x", "source_glob": ""}, "source_glob 必须是非空"),
    ],
)
def test_invalid_overrides_raise_config_error(config_factory, rule, message):
    with pytest.raises(ConfigError, match=message):
        config_factory(column_overrides=[rule])


@pytest.mark.parametrize(
    "value",
    ["not-a-list", {"column_index": 0, "name": "x"}, [42]],
)
def test_overrides_section_shape_is_validated(tmp_path, value):
    path = _write_config(
        tmp_path,
        _base_config_data(tmp_path, column_overrides=value),
    )
    with pytest.raises(ConfigError, match="column_overrides"):
        load_config(path)


def test_null_overrides_section_is_treated_as_absent(tmp_path):
    path = _write_config(
        tmp_path, _base_config_data(tmp_path, column_overrides=None)
    )
    config = load_config(path)
    assert config.column_overrides == ()


def test_strict_config_never_merges_default_overrides(config_factory):
    """strict 只写自己的配置；未写 column_overrides 时不得捡到 default 的规则。"""

    config = config_factory()
    assert config.column_overrides == ()


def test_shipped_configs_have_no_active_overrides():
    for name in ("default.yaml", "strict.yaml"):
        config = load_config(ROOT / "configs" / name)
        assert config.column_overrides == (), name


# ---------------------------------------------------------------------------
# 命中与消解
# ---------------------------------------------------------------------------


def _rule(**kwargs):
    payload = {
        "source_glob": "*",
        "column_name_regex": None,
        "column_index": None,
        "name": None,
        "type": None,
        "priority": 0,
        "order": 0,
    }
    payload.update(kwargs)
    return ColumnOverride(**payload)


def test_globs_are_matched_case_sensitively_against_display_name():
    rule = _rule(source_glob="page_0*.html", column_index=0, name="备注")
    named, typed = select_column_override([rule], "page_04_messy.html", "编号", 0)
    assert named is rule and typed is None
    assert select_column_override([rule], "PAGE_04_messy.html", "编号", 0) == (
        None,
        None,
    )
    assert select_column_override([rule], "page_04_messy.html", "编号", 1) == (
        None,
        None,
    )


def test_name_regex_uses_search_semantics():
    rule = _rule(column_name_regex="金额", name="x")
    # search 语义：子串命中即可，加入 ^/$ 可精确限定
    assert select_column_override([rule], "a.html", "金额_2", 3)[0] is rule
    anchored = _rule(column_name_regex="^金额$", name="x")
    assert select_column_override([anchored], "a.html", "金额_2", 3)[0] is None
    assert select_column_override([anchored], "a.html", "金额", 3)[0] is anchored
    prefix = _rule(column_name_regex="^金额", name="x")
    assert select_column_override([prefix], "a.html", "金额_2", 3)[0] is prefix


def test_selectors_are_combined_with_and():
    rule = _rule(source_glob="page_02.html", column_index=2, name="同比")
    assert select_column_override([rule], "page_02.html", "同比", 2)[0] is rule
    # 文件名、列序任一不满足都不命中
    assert select_column_override([rule], "page_01.html", "同比", 2)[0] is None
    assert select_column_override([rule], "page_02.html", "同比", 1)[0] is None
    # 正则与列序同时给定时也必须同时成立
    both = _rule(column_name_regex="^金额", column_index=2, name="金额甲")
    assert select_column_override([both], "a.html", "金额", 2)[0] is both
    assert select_column_override([both], "a.html", "金额", 3)[0] is None


def test_priority_wins_then_later_order_breaks_ties():
    low = _rule(column_index=0, name="低", priority=1, order=0)
    high = _rule(column_index=0, name="高", priority=9, order=1)
    assert select_column_override([low, high], "a.html", "x", 0)[0] is high
    assert select_column_override([high, low], "a.html", "x", 0)[0] is high

    first = _rule(column_index=0, name="先", priority=4, order=0)
    later = _rule(column_index=0, name="后", priority=4, order=1)
    assert select_column_override([first, later], "a.html", "x", 0)[0] is later
    assert select_column_override([later, first], "a.html", "x", 0)[0] is later


def test_name_and_type_are_resolved_independently():
    namer = _rule(column_index=3, name="合同金额", priority=0, order=0)
    typer = _rule(column_index=3, type="currency", priority=0, order=1)
    named, typed = select_column_override([namer, typer], "a.html", "金额", 3)
    assert named is namer
    assert typed is typer


# ---------------------------------------------------------------------------
# 端到端成功路径
# ---------------------------------------------------------------------------


def test_forced_type_fixes_misjudged_integer_column(config_factory):
    """page_04 的「金额」列被推断成 integer，用规则钉成 currency。"""

    default = build_run(config_factory(), FIXED_NOW)
    baseline = _columns(_table(default, PAGE_04))
    assert baseline["金额"].type == "integer"
    assert _table(default, PAGE_04).records[0]["values"]["金额"] == "1200"

    config = config_factory(
        column_overrides=[
            {
                "source_glob": PAGE_04,
                "column_name_regex": "^金额$",
                "type": "currency",
            }
        ]
    )
    result = build_run(config, FIXED_NOW)
    table = _table(result, PAGE_04)
    column = _columns(table)["金额"]
    assert column.type == "currency"
    assert column.match_ratio == 0.0  # 裸数字不满足 currency 模式
    # 归一化失败：保留原文、无单位，并正常产生 issue
    assert table.records[0]["values"]["金额"] == "1,200"
    assert "金额" not in table.records[0]["units"]
    assert {
        "source_file": PAGE_04,
        "table_index": 0,
        "row_index": 0,
        "col_name": "金额",
        "reason": "number_unparsable",
    } in table.issues


def test_rename_shows_up_in_values_ledger_issues_and_report(config_factory):
    config = config_factory(
        column_overrides=[
            {
                "source_glob": PAGE_04,
                "column_index": 2,
                "name": "备注",
                "type": "text",
            }
        ]
    )
    result = execute_run(config, now=FIXED_NOW)
    table = _table(result, PAGE_04)
    names = [column.name for column in table.columns]
    assert names[2] == "备注"
    assert "col_3" not in names
    assert "备注" in table.records[0]["values"]
    assert "col_3" not in table.records[0]["values"]

    store = Store(config.database)
    ledger = store.columns_for_run(result.run_id)
    ledger_names = [
        row["name"]
        for row in ledger
        if row["source_file"] == PAGE_04 and row["table_index"] == 0
    ]
    assert ledger_names == names

    payload = json.loads(config.report_json.read_text(encoding="utf-8"))
    report_names = [
        column["name"]
        for table_payload in payload["tables"]
        if table_payload["source_file"] == PAGE_04
        for column in table_payload["columns"]
    ]
    assert report_names == names

    # 报告摘要同样使用覆盖后的列名
    summary = summary_for_golden(result)
    assert [
        column["name"]
        for table_payload in summary["tables"]
        if table_payload["source_file"] == PAGE_04
        for column in table_payload["columns"]
    ] == names


def test_report_type_distribution_uses_forced_types(config_factory):
    baseline = summary_for_golden(build_run(config_factory(), FIXED_NOW))
    assert baseline["column_type_distribution"]["integer"] == 4
    assert baseline["column_type_distribution"]["currency"] == 1

    config = config_factory(
        column_overrides=[
            {"source_glob": PAGE_04, "column_index": 3, "type": "currency"}
        ]
    )
    summary = summary_for_golden(build_run(config, FIXED_NOW))
    assert summary["column_type_distribution"]["integer"] == 3
    assert summary["column_type_distribution"]["currency"] == 2


def test_issue_col_name_uses_overridden_name(config_factory):
    config = config_factory(
        column_overrides=[
            {
                "source_glob": PAGE_04,
                "column_name_regex": "^日期$",
                "name": "归档日期",
                "type": "date",
            }
        ]
    )
    result = build_run(config, FIXED_NOW)
    table = _table(result, PAGE_04)
    issues = [issue for issue in table.issues if issue["row_index"] == 3]
    assert issues == [
        {
            "source_file": PAGE_04,
            "table_index": 0,
            "row_index": 3,
            "col_name": "归档日期",
            "reason": "date_unparsable",
        }
    ]
    # 失败格保留清理后的原文，与列名一致地回写
    assert table.records[3]["values"]["归档日期"] == "2026-13-40"


def test_rename_and_type_rules_from_different_entries_combine(config_factory):
    config = config_factory(
        column_overrides=[
            {"source_glob": PAGE_04, "column_index": 3, "name": "合同金额"},
            {"source_glob": PAGE_04, "column_index": 3, "type": "currency"},
        ]
    )
    table = _table(build_run(config, FIXED_NOW), PAGE_04)
    column = _columns(table)["合同金额"]
    assert column.type == "currency"
    assert column.unnamed is False


def test_rename_does_not_double_rename_a_column(config_factory):
    config = config_factory(
        column_overrides=[
            {
                "source_glob": PAGE_04,
                "column_name_regex": "^金额$",
                "name": "合同金额",
            }
        ]
    )
    table = _table(build_run(config, FIXED_NOW), PAGE_04)
    names = [column.name for column in table.columns]
    assert names[3] == "合同金额"
    # 原有「金额_2」保持不变，没有因为改名产生新冲突
    assert names[4] == "金额_2"


def test_override_name_collision_is_resolved_deterministically(config_factory):
    # 把第 2 列改名为与第 0 列同名；覆盖名优先，原列让位为 编号_2
    config = config_factory(
        column_overrides=[
            {"source_glob": PAGE_04, "column_index": 2, "name": "编号"}
        ]
    )
    table = _table(build_run(config, FIXED_NOW), PAGE_04)
    names = [column.name for column in table.columns]
    assert names[0] == "编号_2"
    assert names[2] == "编号"
    assert len(set(names)) == len(names)
    assert set(table.records[0]["values"]) == set(names)


def test_override_only_affects_matching_file_and_column(config_factory):
    config = config_factory(
        column_overrides=[{"source_glob": PAGE_04, "column_index": 2, "name": "备注"}]
    )
    result = build_run(config, FIXED_NOW)
    page_01 = _table(result, "page_01.html")
    assert [column.name for column in page_01.columns] == [
        "项目编号",
        "项目名称",
        "合同金额",
        "完成率",
        "更新日期",
    ]
    assert [column.type for column in page_01.columns] == [
        "identifier",
        "text",
        "currency",
        "percent",
        "date",
    ]


def test_column_index_matches_ledger_col_index(config_factory):
    config = config_factory(
        column_overrides=[{"column_index": 1, "name": "第二列"}]
    )
    result = execute_run(config, now=FIXED_NOW)
    store = Store(config.database)
    for table in result.accepted_tables:
        columns = [c for c in table.columns]
        assert columns[1].name == "第二列"
    ledger = store.columns_for_run(result.run_id)
    renamed = {(row["source_file"], row["table_index"]) for row in ledger if row["name"] == "第二列"}
    assert renamed == {
        (table.source_file, table.table_index) for table in result.accepted_tables
    }


def test_forced_type_ratio_is_recomputed_from_all_cells(config_factory):
    config = config_factory(
        column_overrides=[
            {"source_glob": "page_01.html", "column_index": 2, "type": "currency"}
        ]
    )
    table = _table(build_run(config, FIXED_NOW), "page_01.html")
    column = table.columns[2]
    assert column.type == "currency"
    # 4 个格里有 3 个带货币符号，「300元」「$1,000.00」「¥…」全部命中
    assert column.match_ratio == 1.0


# ---------------------------------------------------------------------------
# 保持原有行为
# ---------------------------------------------------------------------------


def test_empty_overrides_list_is_identical_to_absent(config_factory):
    absent = build_run(config_factory(), FIXED_NOW)
    empty = build_run(config_factory(column_overrides=[]), FIXED_NOW)
    assert summary_for_golden(absent) == summary_for_golden(empty)
    assert absent.all_records() == empty.all_records()
    assert absent.issues == empty.issues


def test_override_that_matches_nothing_changes_nothing(config_factory):
    baseline = build_run(config_factory(), FIXED_NOW)
    config = config_factory(
        column_overrides=[
            {
                "source_glob": "does_not_exist_*.html",
                "column_name_regex": ".*",
                "name": "x",
                "type": "text",
            }
        ]
    )
    noop = build_run(config, FIXED_NOW)
    # 抽取结果完全一致；摘要只多出诊断块，且明确标记该规则未命中
    assert baseline.all_records() == noop.all_records()
    assert baseline.issues == noop.issues
    baseline_summary = summary_for_golden(baseline)
    noop_summary = summary_for_golden(noop)
    assert "column_overrides" not in baseline_summary
    diagnostics = noop_summary.pop("column_overrides")
    assert noop_summary == baseline_summary
    assert diagnostics == [
        {
            "order": 0,
            "source_glob": "does_not_exist_*.html",
            "column_name_regex": ".*",
            "column_index": None,
            "name": "x",
            "type": "text",
            "priority": 0,
            "matched_columns": 0,
            "applied_columns": 0,
            "matches": [],
        }
    ]


def test_override_changes_config_hash_and_run_id(config_factory):
    baseline = build_run(config_factory(), FIXED_NOW)
    overridden = build_run(
        config_factory(
            column_overrides=[{"column_index": 2, "name": "备注"}]
        ),
        FIXED_NOW,
    )
    # 规则本身进入配置哈希，因此 run_id 变化（可复现、可追溯）
    assert overridden.run_id != baseline.run_id
    assert overridden.records_written == baseline.records_written


def test_repeated_override_names_are_suffixed_in_column_order(config_factory):
    config = config_factory(
        column_overrides=[
            {"source_glob": PAGE_04, "column_index": 0, "name": "字段"},
            {"source_glob": PAGE_04, "column_index": 1, "name": "字段"},
        ]
    )
    table = _table(build_run(config, FIXED_NOW), PAGE_04)
    names = [column.name for column in table.columns]
    assert names[:2] == ["字段", "字段_2"]
    assert set(table.records[0]["values"]) == set(names)


def test_type_only_override_keeps_inferred_name_and_unnamed_flag(config_factory):
    # 「col_3」本就是无名列；只定类型时不应擅自把它变成命名列
    config = config_factory(
        column_overrides=[
            {"source_glob": PAGE_04, "column_index": 2, "type": "currency"}
        ]
    )
    table = _table(build_run(config, FIXED_NOW), PAGE_04)
    column = table.columns[2]
    assert column.name == "col_3"
    assert column.unnamed is True
    assert column.type == "currency"
    # 备注文本按 currency 归一化失败，issue 用原列名回写
    issues = [issue for issue in table.issues if issue["col_name"] == "col_3"]
    assert issues and all(issue["reason"] == "number_unparsable" for issue in issues)


def test_report_diagnostics_explain_priority_outcome(config_factory):
    config = config_factory(
        column_overrides=[
            {
                "source_glob": PAGE_04,
                "column_index": 2,
                "name": "低优先级名",
                "priority": 0,
            },
            {
                "source_glob": PAGE_04,
                "column_index": 2,
                "name": "备注",
                "priority": 9,
            },
        ]
    )
    result = build_run(config, FIXED_NOW)
    summary = summary_for_golden(result)
    diagnostics = summary["column_overrides"]
    assert [item["name"] for item in diagnostics] == ["低优先级名", "备注"]
    low, high = diagnostics
    # 两条规则都命中了该列，但只有高优先级那条生效
    assert (low["matched_columns"], low["applied_columns"]) == (1, 0)
    assert (high["matched_columns"], high["applied_columns"]) == (1, 1)
    assert low["matches"][0]["name"] == "备注"
    assert high["matches"][0]["name"] == "备注"
    assert override_warnings(result) == []


def test_override_warnings_report_stale_rules(config_factory):
    config = config_factory(
        column_overrides=[
            {"source_glob": PAGE_04, "column_index": 2, "name": "备注"},
            {"source_glob": "missing.html", "column_index": 0, "name": "x"},
        ]
    )
    result = build_run(config, FIXED_NOW)
    warnings = override_warnings(result)
    assert len(warnings) == 1
    assert "missing.html" in warnings[0]
    assert "未命中任何列" in warnings[0]


def test_no_warnings_without_configured_rules(config_factory):
    assert override_warnings(build_run(config_factory(), FIXED_NOW)) == []


def test_markdown_includes_override_section_only_when_configured(config_factory):
    baseline = build_run(config_factory(), FIXED_NOW)
    configured = build_run(
        config_factory(
            column_overrides=[{"source_glob": PAGE_04, "column_index": 2, "name": "备注"}]
        ),
        FIXED_NOW,
    )
    store = Store(config_factory().database)
    store.initialize()
    assert "## 列覆盖规则" not in render_markdown(build_report(baseline, store, None))
    markdown = render_markdown(build_report(configured, store, None))
    assert "## 列覆盖规则" in markdown
    assert "备注" in markdown


def test_default_and_strict_configs_keep_golden_summary(extracted):
    expected = json.loads(
        (ROOT / "fixtures" / "expected" / "summary.golden.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary_for_golden(extracted) == expected
