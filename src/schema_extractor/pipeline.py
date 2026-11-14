"""配置驱动的确定性抽取管线。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import AppConfig
from .dom import parse_html
from .evidence import EvidenceRecord, write_evidence
from .models import RunResult, TableResult
from .processing import process_table, snapshot_rows
from .report import build_report, write_reports
from .storage import Store
from .tables import extract_tables
from .trace import build_table_trace, build_trace_payload, write_trace

__all__ = [
    "PipelineError",
    "RunResult",
    "TableResult",
    "build_run",
    "canonical_now",
    "execute_run",
    "make_run_id",
    "records_jsonl",
]


class PipelineError(RuntimeError):
    """输入目录缺失、解码失败等运行期错误。"""


def canonical_now(value: str | None = None) -> str:
    if value is None:
        moment = datetime.now(timezone.utc)
    else:
        text = value.strip()
        if not text:
            raise PipelineError("--now 不能为空")
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PipelineError(f"--now 不是合法 ISO-8601 时间: {value}") from exc
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        moment = moment.astimezone(timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def discover_files(config: AppConfig) -> list[tuple[str, Path, bytes]]:
    if not config.input_dir.is_dir():
        raise PipelineError(f"输入目录不存在: {config.input_dir}")
    files: list[tuple[str, Path, bytes]] = []
    for path in sorted(config.input_dir.glob(config.file_glob)):
        if not path.is_file():
            continue
        display = path.relative_to(config.input_dir).as_posix()
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise PipelineError(f"无法读取输入文件 {path}: {exc}") from exc
        files.append((display, path, data))
    return files


def make_run_id(now: str, config_sha256: str, files: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    digest.update(now.encode("utf-8"))
    digest.update(b"\0")
    digest.update(config_sha256.encode("utf-8"))
    for display, data in sorted(files, key=lambda item: item[0]):
        digest.update(b"\0")
        digest.update(display.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).hexdigest().encode("ascii"))
    return digest.hexdigest()[:16]


def _decode(path: Path, data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PipelineError(f"输入文件不是 UTF-8: {path}") from exc


def build_run(config: AppConfig, now: str | None = None) -> RunResult:
    """处理所有输入并返回内存结果；证据会落盘，但不写数据库或报告。"""
    finished = canonical_now(now)
    files = discover_files(config)
    run_id = make_run_id(finished, config.config_sha256, [(d, b) for d, _, b in files])
    result = RunResult(
        run_id=run_id,
        started_at=finished,
        finished_at=finished,
        config_sha256=config.config_sha256,
        files_scanned=len(files),
        tables_detected=0,
    )
    evidence_records: list[EvidenceRecord] = []
    fragments: dict[str, str] = {}
    for source_file, path, data in files:
        source = _decode(path, data)
        document = parse_html(source)
        candidates = extract_tables(document, config.table_selector)
        accepted_here = 0
        records_here = 0
        for candidate in candidates:
            result.tables_detected += 1
            if not candidate.accepted:
                result.filtered_tables.append(
                    {
                        "source_file": source_file,
                        "table_index": candidate.table_index,
                        "reason": candidate.filtered_reason,
                        "notes": list(candidate.notes),
                    }
                )
                result.table_traces.append(
                    build_table_trace(source_file, candidate)
                )
                continue
            table_result, evidence, processing = process_table(
                config, source_file, source, candidate
            )
            raw_table = candidate.node.source_slice(source)
            result.accepted_tables.append(table_result)
            result.issues.extend(table_result.issues)
            result.table_traces.append(
                build_table_trace(
                    source_file, candidate, table_result, processing
                )
            )
            evidence_records.append(evidence)
            fragments[evidence.table_html_sha256] = raw_table
            accepted_here += 1
            records_here += len(table_result.records)
        result.file_summaries.append(
            {
                "source_file": source_file,
                "tables_detected": len(candidates),
                "tables_accepted": accepted_here,
                "records": records_here,
            }
        )
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    result.evidence_index = write_evidence(
        config.evidence_dir, config.evidence_index, evidence_records, fragments
    )
    return result


def execute_run(
    config: AppConfig, now: str | None = None, diff_with: str | None = None
) -> RunResult:
    """完整执行：抽取、证据归档、SQLite UPSERT、报告输出。"""
    result = build_run(config, now)
    store = Store(config.database)
    store.initialize()
    tables, columns, records, issues = snapshot_rows(result)
    store.save_snapshot(
        {
            "run_id": result.run_id,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "config_sha256": result.config_sha256,
            "files_scanned": result.files_scanned,
            "records_written": result.records_written,
            "issues_count": len(result.issues),
        },
        tables,
        columns,
        records,
        issues,
    )
    payload = build_report(result, store, diff_with)
    result.report = payload
    write_reports(payload, config.report_md, config.report_json)
    write_trace(build_trace_payload(result), config.trace_json)
    return result


def records_jsonl(records: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
