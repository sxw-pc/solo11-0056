"""逐表与逐列的确定性决策追踪。

追踪只读取既有判定过程与结果，不参与业务计算，也不改变异常语义。
所有顺序都来自文件顺序、文档顺序、列顺序或行顺序，不依赖集合遍历，
因此同一输入配合同一个 ``--now`` 会产出相同字节。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRACE_VERSION = 1

# 列类型推断的实际分支顺序：前 6 个是优先级检查，后 2 个是回退分支。
INFERENCE_BRANCH_ORDER = (
    "date",
    "percent",
    "currency",
    "integer",
    "decimal",
    "identifier",
    "enum",
    "text",
)


@dataclass(frozen=True)
class HeaderSignal:
    """表头判定信号；evaluated=False 表示前序分支已生效、未再检查。"""

    branch: str
    evaluated: bool
    matched: bool | None
    header_rows: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "evaluated": self.evaluated,
            "matched": self.matched,
            "header_rows": self.header_rows,
        }


@dataclass(frozen=True)
class HeaderTrace:
    strategy: str
    header_row_count: int
    signals: list[HeaderSignal]
    co_matched: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "header_row_count": self.header_row_count,
            "signals": [signal.to_dict() for signal in self.signals],
            "co_matched": list(self.co_matched),
        }


def not_evaluated_header_trace() -> HeaderTrace:
    """嵌套表在解析前即被跳过时使用的占位追踪。"""

    return HeaderTrace(
        "not_evaluated",
        0,
        [
            HeaderSignal("thead", False, None, None),
            HeaderSignal("th", False, None, None),
            HeaderSignal("hint_regex", False, None, None),
        ],
        [],
    )


@dataclass(frozen=True)
class BranchCheck:
    """一次优先级分支检查的命中情况。"""

    branch: str
    matches: int
    ratio: float
    threshold: float
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "matches": self.matches,
            "ratio": self.ratio,
            "threshold": self.threshold,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class ColumnTrace:
    column_index: int
    name: str
    unnamed: bool
    type: str
    branch: str
    branch_kind: str
    priority_index: int | None
    match_ratio: float
    ratio_scope: str
    matched: int | None
    compared: int | None
    non_empty_ratio: float
    non_empty: int
    total: int
    distinct_count: int | None
    sample_values: list[str]
    samples_truncated: bool
    checks: list[BranchCheck]

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_index": self.column_index,
            "name": self.name,
            "unnamed": self.unnamed,
            "type": self.type,
            "inference": {
                "branch": self.branch,
                "branch_kind": self.branch_kind,
                "priority_index": self.priority_index,
                "match_ratio": self.match_ratio,
                "ratio_scope": self.ratio_scope,
                "matched": self.matched,
                "compared": self.compared,
                "non_empty_ratio": self.non_empty_ratio,
                "distinct_count": self.distinct_count,
                "checks": [check.to_dict() for check in self.checks],
            },
            "samples": {
                "count": self.non_empty,
                "shown": len(self.sample_values),
                "truncated": self.samples_truncated,
                "values": list(self.sample_values),
            },
        }


@dataclass(frozen=True)
class FailureStep:
    """规范化失败步骤；只进入追踪，不并入 issues 计数。"""

    row_index: int
    column_index: int
    col_name: str
    column_type: str
    reason: str
    value: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "column_index": self.column_index,
            "col_name": self.col_name,
            "column_type": self.column_type,
            "reason": self.reason,
            "value": self.value,
        }


@dataclass(frozen=True)
class TableProcessingTrace:
    """表格处理阶段收集到的列追踪与规范化失败步骤。"""

    columns: list[ColumnTrace]
    cells: int
    failure_steps: list[FailureStep]


@dataclass(frozen=True)
class TableTrace:
    source_file: str
    table_index: int
    caption: str
    nested: bool
    parsed: bool
    decision: str
    filter_reason: str | None
    notes: list[str]
    header_detection: HeaderTrace
    column_count: int
    row_count: int
    evidence_hash: str | None
    source_line_start: int | None
    source_line_end: int | None
    columns: list[ColumnTrace]
    cells: int
    failure_steps: list[FailureStep]

    def to_dict(self) -> dict[str, Any]:
        evidence = None
        if self.evidence_hash is not None:
            evidence = {
                "hash": self.evidence_hash,
                "source_line_start": self.source_line_start,
                "source_line_end": self.source_line_end,
            }
        return {
            "table_index": self.table_index,
            "caption": self.caption,
            "nested": self.nested,
            "parsed": self.parsed,
            "decision": self.decision,
            "filter_reason": self.filter_reason,
            "notes": list(self.notes),
            "shape": {
                "column_count": self.column_count,
                "row_count": self.row_count,
            },
            "header_detection": self.header_detection.to_dict(),
            "evidence": evidence,
            "columns": [column.to_dict() for column in self.columns],
            "normalization": {
                "cells": self.cells,
                "failures": len(self.failure_steps),
                "steps": [step.to_dict() for step in self.failure_steps],
            },
        }


def build_table_trace(
    source_file: str,
    candidate: Any,
    table_result: Any | None = None,
    processing: TableProcessingTrace | None = None,
) -> TableTrace:
    """从候选表与（可选）处理结果组装单表追踪，不触发新的解析。"""

    accepted = bool(candidate.accepted)
    header_trace = candidate.header_trace
    parsed = header_trace is not None
    if header_trace is None:
        header_trace = not_evaluated_header_trace()
    if accepted:
        if table_result is None or processing is None:
            raise ValueError("采信表格必须同时提供处理结果")
        columns = list(processing.columns)
        cells = processing.cells
        failure_steps = list(processing.failure_steps)
        evidence_hash = table_result.evidence_hash
        source_line_start = table_result.source_line_start
        source_line_end = table_result.source_line_end
    else:
        columns = []
        cells = 0
        failure_steps = []
        evidence_hash = None
        source_line_start = None
        source_line_end = None
    return TableTrace(
        source_file=source_file,
        table_index=candidate.table_index,
        caption=candidate.caption,
        nested=candidate.nested,
        parsed=parsed,
        decision="accepted" if accepted else "filtered",
        filter_reason=candidate.filtered_reason,
        notes=list(candidate.notes),
        header_detection=header_trace,
        column_count=candidate.column_count,
        row_count=len(candidate.data_rows),
        evidence_hash=evidence_hash,
        source_line_start=source_line_start,
        source_line_end=source_line_end,
        columns=columns,
        cells=cells,
        failure_steps=failure_steps,
    )


def build_trace_payload(result: Any) -> dict[str, Any]:
    """把逐表追踪汇总为可写入 report.json 与独立追踪文件的结构。"""

    by_file: dict[str, list[TableTrace]] = {}
    for table_trace in result.table_traces:
        by_file.setdefault(table_trace.source_file, []).append(table_trace)

    files: list[dict[str, Any]] = []
    for summary in result.file_summaries:
        source_file = summary["source_file"]
        traces = by_file.get(source_file, [])
        files.append(
            {
                "source_file": source_file,
                "tables_detected": len(traces),
                "tables_accepted": sum(
                    1 for trace in traces if trace.decision == "accepted"
                ),
                "tables": [trace.to_dict() for trace in traces],
            }
        )

    accepted = [
        trace for trace in result.table_traces if trace.decision == "accepted"
    ]
    return {
        "version": TRACE_VERSION,
        "run_id": result.run_id,
        "generated_at": result.finished_at,
        "counts": {
            "files": len(result.file_summaries),
            "tables": len(result.table_traces),
            "tables_parsed": sum(
                1 for trace in result.table_traces if trace.parsed
            ),
            "tables_accepted": len(accepted),
            "tables_filtered": len(result.table_traces) - len(accepted),
            "columns": sum(len(trace.columns) for trace in accepted),
            "normalization_cells": sum(trace.cells for trace in accepted),
            "normalization_failure_steps": sum(
                len(trace.failure_steps) for trace in accepted
            ),
            "issues": len(result.issues),
        },
        "files": files,
    }


def write_trace(trace: dict[str, Any], path: str | Path) -> None:
    trace_file = Path(path)
    trace_file.parent.mkdir(parents=True, exist_ok=True)
    trace_file.write_text(
        json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
