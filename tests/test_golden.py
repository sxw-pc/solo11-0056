import json
from pathlib import Path

from schema_extractor.pipeline import build_run
from schema_extractor.report import summary_for_golden

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "fixtures" / "expected"


def _load_jsonl(name: str) -> list[dict]:
    text = (EXPECTED / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_page_01_records_match_golden_line_by_line(extracted):
    expected = _load_jsonl("page_01.records.jsonl")
    actual = extracted.records_for("page_01.html")
    assert len(actual) == len(expected) == 4
    for expected_row, actual_row in zip(expected, actual):
        assert actual_row == expected_row


def test_page_02_records_match_golden_line_by_line(extracted):
    expected = _load_jsonl("page_02.records.jsonl")
    actual = extracted.records_for("page_02.html")
    assert len(actual) == len(expected) == 3
    for expected_row, actual_row in zip(expected, actual):
        assert actual_row == expected_row
        assert set(actual_row) == {
            "source_file",
            "table_index",
            "row_index",
            "values",
            "units",
        }


def test_summary_matches_golden(extracted):
    expected = json.loads(
        (EXPECTED / "summary.golden.json").read_text(encoding="utf-8")
    )
    actual = summary_for_golden(extracted)
    assert "run_id" not in actual
    assert "generated_at" not in actual
    assert actual == expected


def test_golden_summary_has_all_files_and_tables(extracted):
    summary = summary_for_golden(extracted)
    assert [item["source_file"] for item in summary["files"]] == [
        "page_01.html",
        "page_02.html",
        "page_03_nested.html",
        "page_04_messy.html",
        "page_05_no_table.html",
        "page_06_mixed.html",
    ]
    assert summary["tables_detected"] == 8
    assert summary["tables_accepted"] == 5
    assert summary["records_written"] == 16


def test_golden_is_stable_across_repeated_builds(default_config):
    first = summary_for_golden(build_run(default_config, "2026-01-01T00:00:00Z"))
    second = summary_for_golden(build_run(default_config, "2026-01-01T00:00:00Z"))
    assert first == second


def test_expected_files_are_valid_utf8_json_or_jsonl():
    for path in EXPECTED.iterdir():
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            assert all(json.loads(line) for line in text.splitlines() if line)
        else:
            assert isinstance(json.loads(text), dict)
