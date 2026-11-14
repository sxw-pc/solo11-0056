"""运行期结果模型。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .header_infer import TYPE_ORDER, ColumnProfile


@dataclass
class TableResult:
    source_file: str
    table_index: int
    caption: str
    columns: list[ColumnProfile]
    records: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    evidence_hash: str
    source_line_start: int
    source_line_end: int
    nested: bool


@dataclass
class RunResult:
    run_id: str
    started_at: str
    finished_at: str
    config_sha256: str
    files_scanned: int
    tables_detected: int
    accepted_tables: list[TableResult] = field(default_factory=list)
    filtered_tables: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    file_summaries: list[dict[str, Any]] = field(default_factory=list)
    evidence_index: dict[str, dict[str, Any]] = field(default_factory=dict)
    report: dict[str, Any] | None = None

    @property
    def records_written(self) -> int:
        return sum(len(table.records) for table in self.accepted_tables)

    def type_distribution(self) -> dict[str, int]:
        counts = Counter(
            column.type
            for table in self.accepted_tables
            for column in table.columns
        )
        return {name: counts[name] for name in TYPE_ORDER if counts.get(name)}

    def all_records(self) -> list[dict[str, Any]]:
        return [record for table in self.accepted_tables for record in table.records]

    def records_for(self, source_file: str) -> list[dict[str, Any]]:
        return [
            record
            for table in self.accepted_tables
            if table.source_file == source_file
            for record in table.records
        ]
