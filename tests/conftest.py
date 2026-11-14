"""共享测试夹具；只使用 tmp_path，不触网。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from schema_extractor.config import AppConfig, load_config  # noqa: E402
from schema_extractor.pipeline import build_run  # noqa: E402

FIXED_NOW = "2026-01-01T00:00:00Z"


@pytest.fixture
def config_factory(tmp_path):
    counter = {"value": 0}

    def make(
        *,
        table_selector: dict | None = None,
        inference: dict | None = None,
        normalization: dict | None = None,
        column_overrides: list | None = None,
        input_dir: str | None = None,
        file_glob: str = "*.html",
    ) -> AppConfig:
        counter["value"] += 1
        index = counter["value"]
        output = tmp_path / f"out_{index}"
        data: dict = {
            "input_dir": input_dir or str(ROOT / "fixtures" / "inputs"),
            "file_glob": file_glob,
            "output_dir": str(output),
            "database": str(output / "runs.sqlite3"),
            "evidence_dir": str(output / "evidence"),
            "evidence_index": str(output / "evidence.json"),
            "report_md": str(output / "report.md"),
            "report_json": str(output / "report.json"),
        }
        if table_selector is not None:
            data["table_selector"] = table_selector
        if inference is not None:
            data["inference"] = inference
        if normalization is not None:
            data["normalization"] = normalization
        if column_overrides is not None:
            data["column_overrides"] = column_overrides
        config_path = tmp_path / f"config_{index}.yaml"
        config_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return load_config(config_path)

    return make


@pytest.fixture
def default_config(config_factory) -> AppConfig:
    return config_factory()


@pytest.fixture
def extracted(default_config):
    return build_run(default_config, FIXED_NOW)


@pytest.fixture
def sample_table_html() -> str:
    return """<html><body>
<table>
  <thead><tr><th>编号</th><th>金额</th></tr></thead>
  <tbody>
    <tr><td>A-01</td><td>¥10</td></tr>
    <tr><td>A-02</td><td>¥20</td></tr>
  </tbody>
</table>
</body></html>"""
