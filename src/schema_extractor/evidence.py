"""原始表格 HTML 证据切片、SHA-256 索引与复现校验。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EvidenceRecord:
    source_file: str
    table_index: int
    source_line_start: int
    source_line_end: int
    table_html_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_file": self.source_file,
            "table_index": self.table_index,
            "source_line_start": self.source_line_start,
            "source_line_end": self.source_line_end,
            "table_html_sha256": self.table_html_sha256,
        }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_evidence(
    source_file: str,
    table_index: int,
    source_line_start: int,
    source_line_end: int,
    table_html: str,
) -> EvidenceRecord:
    return EvidenceRecord(
        source_file=source_file,
        table_index=table_index,
        source_line_start=source_line_start,
        source_line_end=source_line_end,
        table_html_sha256=sha256_text(table_html),
    )


def _index_entry(record: EvidenceRecord) -> dict[str, object]:
    return {
        "source_file": record.source_file,
        "start": record.source_line_start,
        "end": record.source_line_end,
    }


def write_evidence(
    evidence_dir: str | Path,
    index_path: str | Path,
    records: list[EvidenceRecord],
    fragments: dict[str, str],
) -> dict[str, dict[str, object]]:
    """写入证据片段与 hash 索引。相同 hash 只保留一条稳定元数据。"""
    directory = Path(evidence_dir)
    directory.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict[str, object]] = {}
    for record in sorted(records, key=lambda item: (item.source_file, item.table_index)):
        digest = record.table_html_sha256
        if digest not in index:
            index[digest] = _index_entry(record)
        fragment_path = directory / f"{digest}.html"
        fragment_path.write_text(fragments[digest], encoding="utf-8", newline="\n")
    index_file = Path(index_path)
    index_file.parent.mkdir(parents=True, exist_ok=True)
    index_file.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return index


def verify_evidence(evidence_dir: str | Path, record: EvidenceRecord) -> bool:
    """复现检查：证据片段仍存在且 SHA-256 与台账一致。"""
    path = Path(evidence_dir) / f"{record.table_html_sha256}.html"
    if not path.is_file():
        return False
    return hashlib.sha256(path.read_bytes()).hexdigest() == record.table_html_sha256


def load_evidence_index(index_path: str | Path) -> dict[str, dict[str, object]]:
    path = Path(index_path)
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}
    return loaded
