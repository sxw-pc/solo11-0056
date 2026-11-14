"""离线守卫：完整管线运行期间任何出站连接都应立即失败。"""

import socket
import urllib.request

import pytest

from schema_extractor.pipeline import execute_run


def test_full_pipeline_runs_with_network_primitive_blocked(
    monkeypatch, config_factory
):
    def forbidden(*args, **kwargs):
        raise RuntimeError("outbound network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)

    config = config_factory()
    result = execute_run(config, now="2026-01-01T00:00:00Z")

    assert result.files_scanned == 6
    assert result.records_written == 16
    assert config.database.is_file()
    assert config.report_md.is_file()


def test_guard_itself_blocks_connect(monkeypatch):
    def forbidden(*args, **kwargs):
        raise RuntimeError("blocked")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    with socket.socket() as sock:
        with pytest.raises(RuntimeError, match="blocked"):
            sock.connect(("127.0.0.1", 1))
