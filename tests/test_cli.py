import os
import subprocess
import sys
from pathlib import Path

from schema_extractor import cli
from schema_extractor.storage import Store

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + existing if existing else "")
    return env


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "schema_extractor.cli", *args],
        cwd=str(ROOT),
        env=_env(),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def test_cli_run_end_to_end(config_factory):
    config = config_factory()
    completed = _run_cli(
        "run",
        "--config",
        str(config.source_path),
        "--now",
        "2026-01-01T00:00:00Z",
    )
    assert completed.returncode == 0, completed.stderr
    assert "records_written: 16" in completed.stdout
    assert "diff: 无基线" in completed.stdout

    store = Store(config.database)
    runs = store.list_runs()
    assert len(runs) == 1
    assert store.count_records(runs[0]["run_id"]) == 16
    assert len(list(config.evidence_dir.glob("*.html"))) == 5
    assert config.report_md.is_file()
    assert config.report_json.is_file()


def test_cli_list_and_show(config_factory):
    config = config_factory()
    run = _run_cli(
        "run", "--config", str(config.source_path), "--now", "2026-01-01T00:00:00Z"
    )
    assert run.returncode == 0
    run_id = [line for line in run.stdout.splitlines() if line.startswith("run_id:")][
        0
    ].split(":", 1)[1].strip()

    listed = _run_cli("list-runs", "--config", str(config.source_path))
    assert listed.returncode == 0
    assert run_id in listed.stdout
    assert "records" in listed.stdout

    shown = _run_cli(
        "show", "--config", str(config.source_path), "--run-id", run_id
    )
    assert shown.returncode == 0
    assert f"run_id: {run_id}" in shown.stdout
    assert "tables: 5" in shown.stdout
    assert "page_01.html#0" in shown.stdout


def test_cli_diff_with_baseline(config_factory):
    config = config_factory()
    first = _run_cli(
        "run", "--config", str(config.source_path), "--now", "2026-01-01T00:00:00Z"
    )
    run_id = first.stdout.splitlines()[0].split(":", 1)[1].strip()
    second = _run_cli(
        "run",
        "--config",
        str(config.source_path),
        "--now",
        "2026-01-02T00:00:00Z",
        "--diff-with",
        run_id,
    )
    assert second.returncode == 0, second.stderr
    assert "records_delta=+0" in second.stdout


def test_cli_rejects_invalid_now(config_factory):
    config = config_factory()
    completed = _run_cli(
        "run", "--config", str(config.source_path), "--now", "not-a-time"
    )
    assert completed.returncode == 2
    assert "ISO-8601" in completed.stderr


def test_cli_show_missing_run_returns_one(config_factory):
    config = config_factory()
    completed = _run_cli(
        "show", "--config", str(config.source_path), "--run-id", "missing"
    )
    assert completed.returncode == 1
    assert "未找到 run" in completed.stderr


def test_serve_command_uses_local_port(monkeypatch, config_factory):
    config = config_factory()
    captured = {}

    def fake_serve(database, port):
        captured["database"] = database
        captured["port"] = port

    monkeypatch.setattr(cli, "serve", fake_serve)
    assert cli.main(["serve", "--config", str(config.source_path), "--port", "5199"]) == 0
    assert captured["port"] == 5199
    assert str(config.database) == str(captured["database"])
