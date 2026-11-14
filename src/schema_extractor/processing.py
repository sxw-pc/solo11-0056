"""表格到记录、issue 与证据的转换。"""

from __future__ import annotations

from typing import Any

from .config import AppConfig
from .evidence import EvidenceRecord, build_evidence
from .header_infer import TYPE_SOURCE_CONFLICT, ColumnProfile, infer_columns
from .models import RunResult, TableResult
from .normalize import normalize_cell
from .tables import TableCandidate

#: 列级 issue（非某一行的问题）使用的 row_index 哨兵值。
COLUMN_ISSUE_ROW = -1

#: 表头语义与取值证据明显冲突时的 issue reason。
HEADER_VALUE_CONFLICT = "header_value_conflict"


def table_issue(
    table_index: int, source_file: str, row_index: int, column: str, reason: str
) -> dict[str, Any]:
    return {
        "source_file": source_file,
        "table_index": table_index,
        "row_index": row_index,
        "col_name": column,
        "reason": reason,
    }


def column_issues(
    table_index: int, source_file: str, columns: list[ColumnProfile]
) -> list[dict[str, Any]]:
    """表头与取值冲突的列各产生一条列级 issue；顺序随列序，结果确定。"""
    return [
        table_issue(
            table_index, source_file, COLUMN_ISSUE_ROW, column.name, HEADER_VALUE_CONFLICT
        )
        for column in columns
        if column.type_source == TYPE_SOURCE_CONFLICT
    ]


def build_record(
    source_file: str,
    table_index: int,
    row_index: int,
    row: list[str | None],
    columns: list[ColumnProfile],
    date_formats: tuple[str, ...],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    values: dict[str, str] = {}
    units: dict[str, str] = {}
    issues: list[dict[str, Any]] = []
    for index, column in enumerate(columns):
        raw = row[index] if index < len(row) else None
        normalized, unit, reason = normalize_cell(raw, column.type, date_formats)
        values[column.name] = normalized
        if unit:
            units[column.name] = unit
        if reason is not None:
            issues.append(
                table_issue(table_index, source_file, row_index, column.name, reason)
            )
    record = {
        "source_file": source_file,
        "table_index": table_index,
        "row_index": row_index,
        "values": values,
        "units": units,
    }
    return record, issues


def process_table(
    config: AppConfig,
    source_file: str,
    source: str,
    candidate: TableCandidate,
) -> tuple[TableResult, EvidenceRecord]:
    columns = infer_columns(
        candidate.header_names,
        candidate.unnamed_headers,
        candidate.data_rows,
        config.inference,
        config.normalization,
    )
    records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = column_issues(
        candidate.table_index, source_file, columns
    )
    for row_index, row in enumerate(candidate.data_rows):
        record, row_issues = build_record(
            source_file,
            candidate.table_index,
            row_index,
            row,
            columns,
            config.normalization.date_formats,
        )
        records.append(record)
        issues.extend(row_issues)
    raw_table = candidate.node.source_slice(source)
    evidence = build_evidence(
        source_file,
        candidate.table_index,
        candidate.node.line_no,
        candidate.node.end_line,
        raw_table,
    )
    result = TableResult(
        source_file=source_file,
        table_index=candidate.table_index,
        caption=candidate.caption,
        columns=columns,
        records=records,
        issues=issues,
        evidence_hash=evidence.table_html_sha256,
        source_line_start=evidence.source_line_start,
        source_line_end=evidence.source_line_end,
        nested=candidate.nested,
    )
    return result, evidence


def snapshot_rows(
    result: RunResult,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """展开为 SQLite 写入行；保持稳定顺序。"""
    tables: list[dict[str, Any]] = []
    columns: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for table in result.accepted_tables:
        tables.append(
            {
                "run_id": result.run_id,
                "source_file": table.source_file,
                "table_index": table.table_index,
                "evidence_hash": table.evidence_hash,
                "column_count": len(table.columns),
                "row_count": len(table.records),
            }
        )
        for index, column in enumerate(table.columns):
            columns.append(
                {
                    "run_id": result.run_id,
                    "source_file": table.source_file,
                    "table_index": table.table_index,
                    "col_index": index,
                    "name": column.name,
                    "type": column.type,
                    "match_ratio": column.match_ratio,
                    "type_source": column.type_source,
                    "confidence": column.confidence,
                }
            )
        for record in table.records:
            records.append(
                {
                    "run_id": result.run_id,
                    "source_file": table.source_file,
                    "table_index": table.table_index,
                    "row_index": record["row_index"],
                    "values": record["values"],
                    "units": record["units"],
                }
            )
    issues = [{"run_id": result.run_id, **issue} for issue in result.issues]
    return tables, columns, records, issues
