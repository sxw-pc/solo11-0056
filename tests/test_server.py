from schema_extractor.pipeline import execute_run
from schema_extractor.server import MAX_BODY_BYTES, create_app

FIXED_NOW = "2026-01-01T00:00:00Z"


def test_root_lists_runs_and_has_required_title(default_config):
    result = execute_run(default_config, now=FIXED_NOW)
    app = create_app(default_config.database)
    response = app.test_client().get("/")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "标程抽取台" in body
    assert "<title>标程抽取台" in body
    assert result.run_id in body


def test_run_detail_shows_tables_columns_and_issues(default_config):
    result = execute_run(default_config, now=FIXED_NOW)
    app = create_app(default_config.database)
    response = app.test_client().get(f"/run/{result.run_id}")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "page_01.html" in body
    assert "项目编号" in body
    assert "date_unparsable" in body
    assert "Issues 前 20 条" in body


def test_missing_run_returns_404(default_config):
    execute_run(default_config, now=FIXED_NOW)
    app = create_app(default_config.database)
    response = app.test_client().get("/run/not-found")
    assert response.status_code == 404
    assert "未找到 run" in response.get_data(as_text=True)


def test_server_rejects_write_methods(default_config):
    execute_run(default_config, now=FIXED_NOW)
    app = create_app(default_config.database)
    client = app.test_client()
    assert client.post("/").status_code == 405
    assert client.put("/run/anything").status_code == 405
    assert client.delete("/").status_code == 405


def test_request_body_limit_is_one_megabyte(default_config):
    app = create_app(default_config.database)
    assert app.config["MAX_CONTENT_LENGTH"] == MAX_BODY_BYTES == 1024 * 1024


def test_empty_database_renders_empty_state(tmp_path):
    app = create_app(tmp_path / "empty.sqlite3")
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert "暂无 run" in response.get_data(as_text=True)


def test_head_request_is_allowed(default_config):
    app = create_app(default_config.database)
    assert app.test_client().head("/").status_code == 200
