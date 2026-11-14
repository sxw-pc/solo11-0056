import hashlib
import json

from schema_extractor.evidence import (
    build_evidence,
    load_evidence_index,
    sha256_text,
    verify_evidence,
    write_evidence,
)


def test_sha256_text_matches_standard_library():
    assert sha256_text("abc") == hashlib.sha256(b"abc").hexdigest()
    assert sha256_text("中文") == hashlib.sha256("中文".encode("utf-8")).hexdigest()


def test_build_evidence_fields():
    record = build_evidence("page.html", 2, 10, 20, "<table>x</table>")
    assert record.source_file == "page.html"
    assert record.table_index == 2
    assert record.source_line_start == 10
    assert record.source_line_end == 20
    assert record.table_html_sha256 == sha256_text("<table>x</table>")
    assert record.to_dict()["source_line_start"] == 10


def test_write_evidence_and_index(tmp_path):
    record = build_evidence("page.html", 0, 3, 9, "<table>甲</table>")
    index_path = tmp_path / "evidence.json"
    index = write_evidence(
        tmp_path / "evidence",
        index_path,
        [record],
        {record.table_html_sha256: "<table>甲</table>"},
    )
    assert index == {
        record.table_html_sha256: {
            "source_file": "page.html",
            "start": 3,
            "end": 9,
        }
    }
    fragment = tmp_path / "evidence" / f"{record.table_html_sha256}.html"
    assert fragment.read_text(encoding="utf-8") == "<table>甲</table>"
    loaded = json.loads(index_path.read_text(encoding="utf-8"))
    assert loaded == index
    assert verify_evidence(tmp_path / "evidence", record) is True


def test_duplicate_hash_keeps_first_stable_metadata(tmp_path):
    first = build_evidence("a.html", 0, 1, 2, "<table>x</table>")
    second = build_evidence("b.html", 1, 8, 9, "<table>x</table>")
    index = write_evidence(
        tmp_path / "evidence",
        tmp_path / "evidence.json",
        [first, second],
        {first.table_html_sha256: "<table>x</table>"},
    )
    assert len(index) == 1
    assert index[first.table_html_sha256]["source_file"] == "a.html"


def test_verify_detects_tampering(tmp_path):
    record = build_evidence("page.html", 0, 1, 2, "<table>x</table>")
    write_evidence(
        tmp_path,
        tmp_path / "evidence.json",
        [record],
        {record.table_html_sha256: "<table>x</table>"},
    )
    fragment = tmp_path / f"{record.table_html_sha256}.html"
    fragment.write_text("<table>y</table>", encoding="utf-8")
    assert verify_evidence(tmp_path, record) is False
    assert verify_evidence(tmp_path / "absent", record) is False


def test_load_evidence_index_handles_missing_and_invalid(tmp_path):
    assert load_evidence_index(tmp_path / "missing.json") == {}
    invalid = tmp_path / "evidence.json"
    invalid.write_text("[]", encoding="utf-8")
    assert load_evidence_index(invalid) == {}
