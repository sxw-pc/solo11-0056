"""命令行入口：run / serve / show / list-runs。"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .config import AppConfig, ConfigError, load_config
from .pipeline import PipelineError, canonical_now, execute_run
from .server import serve
from .storage import Store

DEFAULT_CONFIG = "configs/default.yaml"


def _print_run_summary(result, config: AppConfig) -> None:
    print(f"run_id: {result.run_id}")
    print(f"files_scanned: {result.files_scanned}")
    print(f"tables_detected: {result.tables_detected}")
    print(f"tables_accepted: {len(result.accepted_tables)}")
    print(f"tables_filtered: {len(result.filtered_tables)}")
    print(f"records_written: {result.records_written}")
    print(f"issues_count: {len(result.issues)}")
    print(f"database: {config.database}")
    print(f"evidence_dir: {config.evidence_dir}")
    print(f"report: {config.report_md}")
    diff = (result.report or {}).get("diff", {})
    if diff.get("status") == "compared":
        print(
            f"diff: records_delta={diff['records_delta']:+d}, "
            f"new_tables={len(diff['new_tables'])}, "
            f"disappeared_tables={len(diff['disappeared_tables'])}"
        )
    else:
        print("diff: 无基线")


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    canonical_now(args.now)  # 提前给出清晰的参数错误
    result = execute_run(config, now=args.now, diff_with=args.diff_with)
    _print_run_summary(result, config)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(f"serving on http://127.0.0.1:{args.port} (read-only)")
    serve(config.database, port=args.port)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = Store(config.database)
    store.initialize()
    run = store.get_run(args.run_id)
    if run is None:
        print(f"未找到 run: {args.run_id}", file=sys.stderr)
        return 1
    print(f"run_id: {run['run_id']}")
    print(f"started_at: {run['started_at']}")
    print(f"finished_at: {run['finished_at']}")
    print(f"config_sha256: {run['config_sha256']}")
    print(f"files_scanned: {run['files_scanned']}")
    print(f"records_written: {run['records_written']}")
    print(f"issues_count: {run['issues_count']}")
    tables = store.tables_for_run(args.run_id)
    print(f"tables: {len(tables)}")
    for table in tables:
        print(
            f"  - {table['source_file']}#{table['table_index']} "
            f"rows={table['row_count']} cols={table['column_count']} "
            f"evidence={table['evidence_hash'][:12]}"
        )
    return 0


def _cmd_list_runs(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = Store(config.database)
    store.initialize()
    runs = store.list_runs()
    if not runs:
        print("暂无 run")
        return 0
    print("run_id\tstarted_at\tfiles\trecords\tissues")
    for run in runs:
        print(
            f"{run['run_id']}\t{run['started_at']}\t{run['files_scanned']}"
            f"\t{run['records_written']}\t{run['issues_count']}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schema-extractor", description="标程抽取台：本地 HTML 表格抽取管线"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="执行一次完整抽取")
    run_parser.add_argument("--config", default=DEFAULT_CONFIG, help="YAML 配置路径")
    run_parser.add_argument("--now", default=None, help="注入 ISO-8601 时间以固定输出")
    run_parser.add_argument("--diff-with", default=None, help="对比基线 run_id")
    run_parser.set_defaults(handler=_cmd_run)

    serve_parser = subparsers.add_parser("serve", help="启动只读本地服务")
    serve_parser.add_argument("--config", default=DEFAULT_CONFIG, help="YAML 配置路径")
    serve_parser.add_argument("--port", type=int, default=5188, help="监听端口")
    serve_parser.set_defaults(handler=_cmd_serve)

    show_parser = subparsers.add_parser("show", help="打印一次 run 的摘要")
    show_parser.add_argument("--run-id", required=True, help="run_id")
    show_parser.add_argument("--config", default=DEFAULT_CONFIG, help="YAML 配置路径")
    show_parser.set_defaults(handler=_cmd_show)

    list_parser = subparsers.add_parser("list-runs", help="列出本地 run")
    list_parser.add_argument("--config", default=DEFAULT_CONFIG, help="YAML 配置路径")
    list_parser.set_defaults(handler=_cmd_list_runs)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (ConfigError, PipelineError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已停止", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
