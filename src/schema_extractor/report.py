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


def _override_diagnostics(result: Any) -> list[dict[str, Any]]:
    """每条覆盖规则的命中情况；无命中时也列出，便于发现写错的过期规则。

    一条规则若命中某列但因优先级落选，``matched_columns`` 仍计入该列，
    同时在 ``applied_columns`` 中单独统计最终生效的列数，便于解释冲突。
    """
    configured = list(getattr(result, "column_overrides", ()))
    if not configured:
        return []
    hits = [
        {"source_file": table.source_file, "table_index": table.table_index, **item}
        for table in result.accepted_tables
        for item in table.override_applied
    ]
    diagnostics: list[dict[str, Any]] = []
    for rule in configured:
        selectors = rule.selectors_to_dict()
        key = rule.selectors_key
        matched = [item for item in hits if key in item["matched_rules"]]
        applied = [
            item
            for item in matched
            if key in (item["applied_name_rule"], item["applied_type_rule"])
        ]
        diagnostics.append(
            {
                **selectors,
                "matched_columns": len(matched),
                "applied_columns": len(applied),
                "matches": [
                    {
                        "source_file": item["source_file"],
                        "table_index": item["table_index"],
                        "col_index": item["col_index"],
                        "original_name": item["original_name"],
                        "original_type": item["original_type"],
                        "name": item["name"],
                        "type": item["type"],
                        "collision_resolved": item["collision_resolved"],
                    }
                    for item in matched
                ],
            }
        )
    return diagnostics


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
    summary: dict[str, Any] = {
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
    diagnostics = _override_diagnostics(result)
    if diagnostics:
        # 只有配置了覆盖规则时才出现，未配置时摘要与旧版本逐字段一致。
        summary["column_overrides"] = diagnostics
    return summary


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


def _override_rows(payload: dict[str, Any]) -> list[str]:
    diagnostics = payload.get("column_overrides")
    if not diagnostics:
        return []
    lines = [
        "## 列覆盖规则",
        "",
        "| source_glob | 列名正则 | 列序 | 覆盖 name | 覆盖 type | priority | 命中列数 | 生效列数 |",
        "|---|---|---:|---|---:|---:|---:|---:|",
    ]
    for item in diagnostics:
        lines.append(
            f"| `{item['source_glob']}` "
            f"| `{item['column_name_regex'] or '-'}` "
            f"| {item['column_index'] if item['column_index'] is not None else '-'} "
            f"| {item['name'] or '-'} "
            f"| {item['type'] or '-'} "
            f"| {item['priority']} "
            f"| {item['matched_columns']} "
            f"| {item['applied_columns']} |"
        )
    stale = [item for item in diagnostics if item["matched_columns"] == 0]
    if stale:
        lines += [
            "",
            f"> 有 {len(stale)} 条规则未命中任何列，可能已随列名或文件重命名失效。",
        ]
    lines.append("")
    return lines


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
        *_override_rows(payload),
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
