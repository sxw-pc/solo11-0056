"""仅本机监听的只读查看服务。"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

from flask import Flask, Response, request

from .storage import Store

PAGE_TITLE = "标程抽取台"
MAX_BODY_BYTES = 1024 * 1024

_STYLE = """
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:2rem;
color:#1f2933;background:#f7f9fb}
main{max-width:1080px;margin:0 auto}
h1,h2{color:#12344d}
table{border-collapse:collapse;width:100%;background:#fff;margin:.6rem 0 1.4rem}
th,td{border:1px solid #d8e0e8;padding:.45rem .6rem;text-align:left;
vertical-align:top}
th{background:#eaf1f6}
code{background:#eef2f5;padding:.1rem .3rem;border-radius:4px}
.muted{color:#647687}
.notice{border-left:4px solid #3d7ea6;background:#fff;padding:.6rem .8rem}
"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(PAGE_TITLE)} · {escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body><main>
{body}
<p class="muted">只读本地服务。无写入接口，不发起任何外部请求。</p>
</main></body></html>"""


def _runs_table(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return '<p class="notice">暂无 run。请先执行抽取命令。</p>'
    rows = []
    for run in runs:
        run_id = escape(str(run["run_id"]))
        rows.append(
            "<tr>"
            f'<td><a href="/run/{quote(str(run["run_id"]))}"><code>{run_id}</code></a></td>'
            f"<td>{escape(str(run['started_at']))}</td>"
            f"<td>{run['files_scanned']}</td>"
            f"<td>{run['records_written']}</td>"
            f"<td>{run['issues_count']}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>run_id</th><th>开始时间</th><th>文件数</th>"
        "<th>记录数</th><th>issues</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _columns_rows(columns: list[dict[str, Any]]) -> str:
    return "".join(
        "<tr>"
        f"<td>{column['col_index']}</td>"
        f"<td>{escape(str(column['name']))}</td>"
        f"<td><code>{escape(str(column['type']))}</code></td>"
        f"<td>{float(column['match_ratio']):.2%}</td>"
        "</tr>"
        for column in columns
    )


def _tables_section(
    tables: list[dict[str, Any]], columns: list[dict[str, Any]]
) -> str:
    if not tables:
        return "<p>该 run 没有采信表格。</p>"
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for column in columns:
        key = (str(column["source_file"]), int(column["table_index"]))
        grouped.setdefault(key, []).append(column)
    blocks = []
    for table in tables:
        key = (str(table["source_file"]), int(table["table_index"]))
        digest = escape(str(table["evidence_hash"]))
        blocks.append(
            f"<h2>{escape(key[0])} · 表 {key[1]}</h2>"
            "<table><thead><tr><th>#</th><th>列名</th><th>类型</th>"
            "<th>match_ratio</th></tr></thead><tbody>"
            + _columns_rows(grouped.get(key, []))
            + "</tbody></table>"
            f"<p>记录数：{table['row_count']}；证据：<code>{digest[:16]}</code></p>"
        )
    return "".join(blocks)


def _issues_section(issues: list[dict[str, Any]]) -> str:
    if not issues:
        return "<p>没有 issues。</p>"
    rows = "".join(
        "<tr>"
        f"<td>{escape(str(issue['source_file']))}</td>"
        f"<td>{issue['table_index']}</td>"
        f"<td>{issue['row_index']}</td>"
        f"<td>{escape(str(issue['col_name']))}</td>"
        f"<td><code>{escape(str(issue['reason']))}</code></td>"
        "</tr>"
        for issue in issues
    )
    return (
        "<table><thead><tr><th>文件</th><th>表</th><th>行</th><th>列</th>"
        f"<th>reason</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _run_page(store: Store, run_id: str) -> str:
    run = store.get_run(run_id)
    if run is None:
        return '<h1>未找到 run</h1><p><a href="/">返回列表</a></p>'
    tables = store.tables_for_run(run_id)
    columns = store.columns_for_run(run_id)
    issues = store.issues_for_run(run_id, limit=20)
    run_label = escape(str(run["run_id"]))
    return (
        f'<p><a href="/">← run 列表</a></p><h1>{PAGE_TITLE}</h1>'
        f"<p>run：<code>{run_label}</code>；开始于 {escape(str(run['started_at']))}</p>"
        f"<p>文件 {run['files_scanned']} 个；记录 {run['records_written']} 条；"
        f"issues {run['issues_count']} 条。</p>"
        "<h2>表格与列推断</h2>"
        + _tables_section(tables, columns)
        + "<h2>Issues 前 20 条</h2>"
        + _issues_section(issues)
    )


def create_app(database: str | Path) -> Flask:
    store = Store(database)
    store.initialize()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES

    @app.before_request
    def reject_write_methods() -> Response | None:
        if request.method not in {"GET", "HEAD"}:
            return Response("只读服务，不提供写入接口。", status=405, mimetype="text/plain")
        return None

    @app.get("/")
    def index() -> str:
        body = f"<h1>{PAGE_TITLE}</h1><p>本地 HTML 表格抽取 run 列表。</p>"
        body += _runs_table(store.list_runs())
        return _page("run 列表", body)

    @app.get("/run/<run_id>")
    def run_detail(run_id: str) -> tuple[str, int] | str:
        page = _run_page(store, run_id)
        status = 404 if "未找到" in page else 200
        return _page("run 详情", page), status

    return app


def serve(database: str | Path, port: int = 5188) -> None:
    """仅绑定 127.0.0.1，关闭重载器，避免任何网络暴露。"""
    app = create_app(database)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
