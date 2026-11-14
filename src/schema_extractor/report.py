"""抽取质量报告：JSON 归档与 Markdown 可读版本。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .storage import Store

TOP_ISSUES = 10


def _issue_rollup(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(issue["reason"] for issue in issues)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"reason": reason, "count": count} for reason, count in ordered[:TOP_ISSUES]]


def _issue_map(issues: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(issue["reason"] for issue in issues).items()))


def _evidence_coverage(result: Any) -> dict[str, Any]:
    total = len(result.accepted_tables)
    with_hash = sum(1 for table in result.accepted_tables if table.evidence_hash)
    ratio = round(with_hash / total, 4) if total else 0.0
    return {"tables_with_hash": with_hash, "tables_total": total, "ratio": ratio}


def build_diff(result: Any, store: Store, baseline_run_id: str | None) -> dict[str, Any]:
    current_records = result.records_written
    if not baseline_run_id:
        return {
            "status": "no_baseline",
            "baseline_run_id": None,
            "message": "无基线",
            "records_delta": None,
            "new_tables": [],
            "disappeared_tables": [],
            "current_records": current_records,
        }
    baseline = store.get_run(baseline_run_id)
    if baseline is None:
        return {
            "status": "no_baseline",
            "baseline_run_id": baseline_run_id,
            "message": "无基线",
            "records_delta": None,
            "new_tables": [],
            "disappeared_tables": [],
            "current_records": current_records,
        }
    current_keys = {
        f"{table.source_file}#{table.table_index}" for table in result.accepted_tables
    }
    baseline_keys = store.table_keys(baseline_run_id)
    return {
        "status": "compared",
        "baseline_run_id": baseline_run_id,
        "message": None,
        "records_delta": current_records - int(baseline["records_written"]),
        "baseline_records": int(baseline["records_written"]),
        "current_records": current_records,
        "new_tables": sorted(current_keys - baseline_keys),
        "disappeared_tables": sorted(baseline_keys - current_keys),
    }


def summary_for_golden(result: Any) -> dict[str, Any]:
    """不包含 run_id / generated_at 的稳定摘要，供 golden 测试逐字段比较。"""
    tables = []
    for table in result.accepted_tables:
        tables.append(
            {
                "source_file": table.source_file,
                "table_index": table.table_index,
                "row_count": len(table.records),
                "column_count": len(table.columns),
                "evidence_hash": table.evidence_hash,
                "columns": [column.to_dict() for column in table.columns],
            }
        )
    return {
        "files_scanned": result.files_scanned,
        "tables_detected": result.tables_detected,
        "tables_accepted": len(result.accepted_tables),
        "tables_filtered": [
            {
                "source_file": item["source_file"],
                "table_index": item["table_index"],
                "reason": item["reason"],
            }
            for item in result.filtered_tables
        ],
        "records_written": result.records_written,
        "issues_count": len(result.issues),
        "column_type_distribution": result.type_distribution(),
        "issues_by_reason": _issue_map(result.issues),
        "evidence_coverage": _evidence_coverage(result),
        "files": result.file_summaries,
        "tables": tables,
    }


def build_report(
    result: Any, store: Store, baseline_run_id: str | None = None
) -> dict[str, Any]:
    payload = summary_for_golden(result)
    payload.update(
        {
            "run_id": result.run_id,
            "generated_at": result.finished_at,
            "started_at": result.started_at,
            "config_sha256": result.config_sha256,
            "tables_filtered_count": len(result.filtered_tables),
            "issues_top": _issue_rollup(result.issues),
            "diff": build_diff(result, store, baseline_run_id),
        }
    )
    return payload


def _table_rows(payload: dict[str, Any]) -> list[str]:
    lines = [
        "| 文件 | 表序号 | 行数 | 列数 | 证据 hash（前 12 位） |",
        "|---|---:|---:|---:|---|",
    ]
    for table in payload["tables"]:
        digest = str(table["evidence_hash"])[:12]
        lines.append(
            f"| {table['source_file']} | {table['table_index']} | "
            f"{table['row_count']} | {table['column_count']} | `{digest}` |"
        )
    if len(lines) == 2:
        lines.append("| （无） | - | - | - | - |")
    return lines


def _filtered_rows(payload: dict[str, Any]) -> list[str]:
    if not payload["tables_filtered"]:
        return ["- 无"]
    return [
        f"- `{item['source_file']}` 表 {item['table_index']}：`{item['reason']}`"
        for item in payload["tables_filtered"]
    ]


def _type_rows(payload: dict[str, Any]) -> list[str]:
    distribution = payload["column_type_distribution"]
    lines = ["| 类型 | 列数 |", "|---|---:|"]
    if not distribution:
        return lines + ["| （无） | 0 |"]
    lines.extend(
        f"| {type_name} | {count} |"
        for type_name, count in sorted(distribution.items())
    )
    return lines


def _issue_rows(payload: dict[str, Any]) -> list[str]:
    if not payload["issues_top"]:
        return ["- 无"]
    return [
        f"- `{item['reason']}`：{item['count']}"
        for item in payload["issues_top"]
    ]


def _diff_section(payload: dict[str, Any]) -> list[str]:
    diff = payload["diff"]
    if diff["status"] == "no_baseline":
        return ["无基线。"]
    return [
        f"- 基线 run：`{diff['baseline_run_id']}`",
        f"- 记录总数变化：{diff['records_delta']:+d}"
        f"（{diff['baseline_records']} → {diff['current_records']}）",
        f"- 新增表：{', '.join(diff['new_tables']) or '无'}",
        f"- 消失表：{', '.join(diff['disappeared_tables']) or '无'}",
    ]


def render_markdown(payload: dict[str, Any]) -> str:
    coverage = payload["evidence_coverage"]
    lines = [
        "# 标程抽取台抽取质量报告",
        "",
        f"- run_id：`{payload['run_id']}`",
        f"- 生成时间：{payload['generated_at']}",
        f"- 配置 SHA-256：`{payload['config_sha256']}`",
        "",
        "## 概览",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 文件处理数 | {payload['files_scanned']} |",
        f"| 识别表格数 | {payload['tables_detected']} |",
        f"| 采信表格数 | {payload['tables_accepted']} |",
        f"| 被过滤表格数 | {payload['tables_filtered_count']} |",
        f"| 记录数 | {payload['records_written']} |",
        f"| issues 数 | {payload['issues_count']} |",
        "",
        "## 采信表格",
        "",
        *_table_rows(payload),
        "",
        "## 列类型分布",
        "",
        *_type_rows(payload),
        "",
        "## Issues Top",
        "",
        *_issue_rows(payload),
        "",
        "## 证据覆盖",
        "",
        f"- 有 hash 的表：{coverage['tables_with_hash']} / {coverage['tables_total']}",
        f"- 覆盖率：{coverage['ratio']:.2%}",
        "",
        "## 被过滤表格",
        "",
        *_filtered_rows(payload),
        "",
        "## 与上一次 run 差异",
        "",
        *_diff_section(payload),
        "",
    ]
    return "\n".join(lines)


def write_reports(
    payload: dict[str, Any], markdown_path: str | Path, json_path: str | Path
) -> None:
    markdown_file = Path(markdown_path)
    markdown_file.parent.mkdir(parents=True, exist_ok=True)
    markdown_file.write_text(
        render_markdown(payload), encoding="utf-8", newline="\n"
    )
    json_file = Path(json_path)
    json_file.parent.mkdir(parents=True, exist_ok=True)
    json_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
