import json
import sqlite3

from schema_extractor.storage import SCHEMA, Store


def _snapshot(run_id: str = "run-1", records: int = 2):
    run = {
        "run_id": run_id,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:00Z",
        "config_sha256": "abc123",
        "files_scanned": 1,
        "records_written": records,
        "issues_count": 1,
    }
    tables = [
        {
            "run_id": run_id,
            "source_file": "page.html",
            "table_index": 0,
            "evidence_hash": "hash-1",
            "column_count": 2,
            "row_count": records,
        }
    ]
    columns = [
        {
            "run_id": run_id,
            "source_file": "page.html",
            "table_index": 0,
            "col_index": index,
            "name": name,
            "type": type_name,
            "match_ratio": 1.0,
        }
        for index, (name, type_name) in enumerate(
            [("编号", "identifier"), ("金额", "currency")]
        )
    ]
    record_rows = [
        {
            "run_id": run_id,
            "source_file": "page.html",
            "table_index": 0,
            "row_index": index,
            "values": {"编号": f"A-0{index + 1}", "金额": str(10 + index)},
            "units": {"金额": "CNY"},
        }
        for index in range(records)
    ]
    issues = [
        {
            "run_id": run_id,
            "source_file": "page.html",
            "table_index": 0,
            "row_index": 0,
            "col_name": "金额",
            "reason": "number_unparsable",
        }
    ]
    return run, tables, columns, record_rows, issues


def test_initialize_creates_all_tables(tmp_path):
    store = Store(tmp_path / "runs.sqlite3")
    store.initialize()
    with sqlite3.connect(store.database) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"runs", "tables", "columns", "records", "issues"} <= names


def test_save_snapshot_and_queries(tmp_path):
    store = Store(tmp_path / "runs.sqlite3")
    store.initialize()
    store.save_snapshot(*_snapshot())
    run = store.get_run("run-1")
    assert run["records_written"] == 2
    assert run["config_sha256"] == "abc123"
    assert len(store.tables_for_run("run-1")) == 1
    assert len(store.columns_for_run("run-1")) == 2
    assert store.count_records("run-1") == 2
    assert store.table_keys("run-1") == {"page.html#0"}
    assert store.issues_for_run("run-1")[0]["reason"] == "number_unparsable"


def test_same_run_id_does_not_duplicate_rows(tmp_path):
    store = Store(tmp_path / "runs.sqlite3")
    store.initialize()
    store.save_snapshot(*_snapshot())
    store.save_snapshot(*_snapshot())
    assert len(store.list_runs()) == 1
    assert len(store.tables_for_run("run-1")) == 1
    assert len(store.columns_for_run("run-1")) == 2
    assert store.count_records("run-1") == 2
    assert len(store.issues_for_run("run-1")) == 1


def test_records_store_values_and_units_json(tmp_path):
    store = Store(tmp_path / "runs.sqlite3")
    store.initialize()
    store.save_snapshot(*_snapshot())
    with sqlite3.connect(store.database) as connection:
        payload = connection.execute(
            "SELECT data_json FROM records WHERE run_id='run-1' AND row_index=0"
        ).fetchone()[0]
    decoded = json.loads(payload)
    assert decoded["values"]["编号"] == "A-01"
    assert decoded["units"]["金额"] == "CNY"


def test_list_runs_orders_by_time_then_id(tmp_path):
    store = Store(tmp_path / "runs.sqlite3")
    store.initialize()
    first = _snapshot("run-a")
    first[0]["started_at"] = "2026-01-01T00:00:00Z"
    second = _snapshot("run-b")
    second[0]["started_at"] = "2026-01-02T00:00:00Z"
    store.save_snapshot(*first)
    store.save_snapshot(*second)
    assert [run["run_id"] for run in store.list_runs()] == ["run-b", "run-a"]


def test_missing_run_returns_none_and_empty_collections(tmp_path):
    store = Store(tmp_path / "runs.sqlite3")
    store.initialize()
    assert store.get_run("missing") is None
    assert store.tables_for_run("missing") == []
    assert store.columns_for_run("missing") == []
    assert store.issues_for_run("missing") == []
    assert store.table_keys("missing") == set()
    assert store.table_row_counts("missing") == {}
    assert store.columns_by_table("missing") == {}


def test_table_row_counts_and_columns_by_table(tmp_path):
    store = Store(tmp_path / "runs.sqlite3")
    store.initialize()
    store.save_snapshot(*_snapshot())
    assert store.table_row_counts("run-1") == {"page.html#0": 2}
    assert store.columns_by_table("run-1") == {
        "page.html#0": [
            {"col_index": 0, "name": "编号", "type": "identifier"},
            {"col_index": 1, "name": "金额", "type": "currency"},
        ]
    }


def test_issues_limit_and_ordering(tmp_path):
    store = Store(tmp_path / "runs.sqlite3")
    store.initialize()
    run, tables, columns, records, issues = _snapshot(records=3)
    issues = [
        {**issues[0], "row_index": index, "col_name": f"c{index}"}
        for index in range(3)
    ]
    store.save_snapshot(run, tables, columns, records, issues)
    assert len(store.issues_for_run("run-1", limit=2)) == 2
    assert [row["row_index"] for row in store.issues_for_run("run-1")] == [0, 1, 2]


def test_schema_contains_primary_keys():
    assert "PRIMARY KEY (run_id, source_file, table_index, row_index)" in SCHEMA
    assert "PRIMARY KEY (run_id, source_file, table_index, col_index)" in SCHEMA
