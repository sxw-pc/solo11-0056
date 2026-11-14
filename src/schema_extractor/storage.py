"""SQLite 台账：幂等写入与只读查询。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    config_sha256 TEXT NOT NULL,
    files_scanned INTEGER NOT NULL,
    records_written INTEGER NOT NULL,
    issues_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tables (
    run_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    table_index INTEGER NOT NULL,
    evidence_hash TEXT NOT NULL,
    column_count INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    PRIMARY KEY (run_id, source_file, table_index)
);
CREATE TABLE IF NOT EXISTS columns (
    run_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    table_index INTEGER NOT NULL,
    col_index INTEGER NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    match_ratio REAL NOT NULL,
    PRIMARY KEY (run_id, source_file, table_index, col_index)
);
CREATE TABLE IF NOT EXISTS records (
    run_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    table_index INTEGER NOT NULL,
    row_index INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (run_id, source_file, table_index, row_index)
);
CREATE TABLE IF NOT EXISTS issues (
    run_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    table_index INTEGER NOT NULL,
    row_index INTEGER NOT NULL,
    col_name TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (run_id, source_file, table_index, row_index, col_name)
);
CREATE INDEX IF NOT EXISTS idx_tables_run ON tables(run_id);
CREATE INDEX IF NOT EXISTS idx_records_run ON records(run_id);
CREATE INDEX IF NOT EXISTS idx_issues_run ON issues(run_id);
"""


class Store:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database))
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def save_snapshot(
        self,
        run: dict[str, Any],
        tables: Iterable[dict[str, Any]],
        columns: Iterable[dict[str, Any]],
        records: Iterable[dict[str, Any]],
        issues: Iterable[dict[str, Any]],
    ) -> None:
        """整次 run 使用 UPSERT；同一 run_id 重跑不会产生重复行。"""
        table_rows = list(tables)
        column_rows = list(columns)
        record_rows = []
        for record in records:
            row = dict(record)
            payload: dict[str, Any] = {"values": row.pop("values", {})}
            units = row.pop("units", {})
            if units:
                payload["units"] = units
            date_precision = row.pop("date_precision", {})
            if date_precision:
                payload["date_precision"] = date_precision
            row["data_json"] = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            record_rows.append(row)
        issue_rows = list(issues)
        with self.connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO runs
                (run_id, started_at, finished_at, config_sha256,
                 files_scanned, records_written, issues_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    run["run_id"],
                    run["started_at"],
                    run["finished_at"],
                    run["config_sha256"],
                    run["files_scanned"],
                    run["records_written"],
                    run["issues_count"],
                ),
            )
            connection.executemany(
                """INSERT OR REPLACE INTO tables
                (run_id, source_file, table_index, evidence_hash,
                 column_count, row_count)
                VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        row["run_id"],
                        row["source_file"],
                        row["table_index"],
                        row["evidence_hash"],
                        row["column_count"],
                        row["row_count"],
                    )
                    for row in table_rows
                ],
            )
            connection.executemany(
                """INSERT OR REPLACE INTO columns
                (run_id, source_file, table_index, col_index,
                 name, type, match_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        row["run_id"],
                        row["source_file"],
                        row["table_index"],
                        row["col_index"],
                        row["name"],
                        row["type"],
                        row["match_ratio"],
                    )
                    for row in column_rows
                ],
            )
            connection.executemany(
                """INSERT OR REPLACE INTO records
                (run_id, source_file, table_index, row_index, data_json)
                VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        row["run_id"],
                        row["source_file"],
                        row["table_index"],
                        row["row_index"],
                        row["data_json"],
                    )
                    for row in record_rows
                ],
            )
            connection.executemany(
                """INSERT OR REPLACE INTO issues
                (run_id, source_file, table_index, row_index, col_name, reason)
                VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        row["run_id"],
                        row["source_file"],
                        row["table_index"],
                        row["row_index"],
                        row["col_name"],
                        row["reason"],
                    )
                    for row in issue_rows
                ],
            )

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT run_id, started_at, finished_at, config_sha256,
                          files_scanned, records_written, issues_count
                   FROM runs ORDER BY started_at DESC, run_id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def tables_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT source_file, table_index, evidence_hash,
                          column_count, row_count
                   FROM tables WHERE run_id = ?
                   ORDER BY source_file, table_index""",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def columns_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT source_file, table_index, col_index, name, type, match_ratio
                   FROM columns WHERE run_id = ?
                   ORDER BY source_file, table_index, col_index""",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def issues_for_run(self, run_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT source_file, table_index, row_index, col_name, reason
                   FROM issues WHERE run_id = ?
                   ORDER BY source_file, table_index, row_index, col_name
                   LIMIT ?""",
                (run_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_records(self, run_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM records WHERE run_id = ?", (run_id,)
            ).fetchone()
        return int(row["count"])

    def table_keys(self, run_id: str) -> set[str]:
        return {
            f"{row['source_file']}#{row['table_index']}"
            for row in self.tables_for_run(run_id)
        }
