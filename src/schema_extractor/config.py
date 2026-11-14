"""YAML 配置加载、校验与稳定哈希。

配置只描述本地批处理参数，不包含任何网络、账户或密钥字段。
"""

from __future__ import annotations

import copy
import fnmatch
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 列类型顺序同时决定报告的分布排序；覆盖规则也复用同一份取值集合。
COLUMN_TYPE_ORDER = (
    "identifier",
    "currency",
    "percent",
    "integer",
    "decimal",
    "date",
    "enum",
    "text",
)


class ConfigError(ValueError):
    """配置缺失、类型错误或取值非法。"""


DEFAULT_CONFIG: dict[str, Any] = {
    "project_name": "标程抽取台",
    "input_dir": "fixtures/inputs",
    "file_glob": "*.html",
    "output_dir": "build/default",
    "database": "build/default/runs.sqlite3",
    "evidence_dir": "build/default/evidence",
    "evidence_index": "build/default/evidence.json",
    "report_md": "build/default/report.md",
    "report_json": "build/default/report.json",
    "table_selector": {
        "min_rows": 1,
        "min_cols": 2,
        "require_header": True,
        "header_hint_regex": (
            "^(?:序号|编号|ID|名称|项目|日期|金额|数量|比例|状态|类型|区域|指标|单位)"
        ),
        "nested_table_policy": "skip",
    },
    "inference": {
        "min_match_ratio": 0.75,
        "min_non_empty_ratio": 0.5,
        "max_enum_cardinality": 8,
        "max_sample_values": 3,
        "identifier_pattern": r"^[A-Za-z]{1,12}[-_]?\d{2,}(?:[-_]\d+)*$",
    },
    "normalization": {
        "date_formats": ["%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"],
    },
}

_TOP_LEVEL_KEYS = set(DEFAULT_CONFIG) | {"column_overrides"}
_TABLE_KEYS = set(DEFAULT_CONFIG["table_selector"])
_INFERENCE_KEYS = set(DEFAULT_CONFIG["inference"])
_NORMALIZATION_KEYS = set(DEFAULT_CONFIG["normalization"])

_OVERRIDE_KEYS = frozenset(
    {
        "source_glob",
        "column_name_regex",
        "column_index",
        "name",
        "type",
        "priority",
    }
)


@dataclass(frozen=True)
class ColumnOverride:
    """一条列覆盖规则。

    选择器之间是 AND 关系：``source_glob``（缺省 ``*``）与
    ``column_name_regex`` / ``column_index`` 同时命中才算命中。
    ``column_name_regex`` 对**推断后的原始列名**做 ``re.search``（正则默认
    语义），因此 ``^金额`` 可命中所有以「金额」开头的列；需要精确匹配时在
    自己写的表达式末尾加 ``$``。``column_index`` 为 0 基列序，与 SQLite
    台账的 ``col_index`` 一致。

    一列最多采纳一条规则：同一列被多条规则命中时取 ``(priority, order)``
    最大者，即 ``priority`` 数值大者优先，同优先级时配置中靠后的规则胜出。
    """

    source_glob: str
    column_name_regex: str | None
    column_index: int | None
    name: str | None
    type: str | None
    priority: int
    order: int

    @property
    def name_pattern(self) -> re.Pattern[str] | None:
        if self.column_name_regex is None:
            return None
        return re.compile(self.column_name_regex)

    def matches(self, source_file: str, column_name: str, col_index: int) -> bool:
        if not fnmatch.fnmatchcase(source_file, self.source_glob):
            return False
        if self.column_index is not None and self.column_index != col_index:
            return False
        pattern = self.name_pattern
        if pattern is not None and pattern.search(column_name) is None:
            return False
        return True

    def selectors_to_dict(self) -> dict[str, object]:
        """规则的稳定描述，用于报告中的命中诊断。``order`` 是配置中的位置。"""
        return {
            "order": self.order,
            "source_glob": self.source_glob,
            "column_name_regex": self.column_name_regex,
            "column_index": self.column_index,
            "name": self.name,
            "type": self.type,
            "priority": self.priority,
        }

    @property
    def selectors_key(self) -> tuple[tuple[str, object], ...]:
        """``selectors_to_dict`` 的可哈希形式，便于按规则统计命中。"""
        return tuple(sorted(self.selectors_to_dict().items()))


@dataclass(frozen=True)
class TableSelector:
    min_rows: int
    min_cols: int
    require_header: bool
    header_hint_regex: str
    nested_table_policy: str

    @property
    def hint_pattern(self) -> re.Pattern[str]:
        return re.compile(self.header_hint_regex)


@dataclass(frozen=True)
class InferenceOptions:
    min_match_ratio: float
    min_non_empty_ratio: float
    max_enum_cardinality: int
    max_sample_values: int
    identifier_pattern: str

    @property
    def identifier_re(self) -> re.Pattern[str]:
        return re.compile(self.identifier_pattern)


@dataclass(frozen=True)
class NormalizationOptions:
    date_formats: tuple[str, ...]


@dataclass(frozen=True)
class AppConfig:
    project_name: str
    input_dir: Path
    file_glob: str
    output_dir: Path
    database: Path
    evidence_dir: Path
    evidence_index: Path
    report_md: Path
    report_json: Path
    table_selector: TableSelector
    inference: InferenceOptions
    normalization: NormalizationOptions
    config_sha256: str
    source_path: Path
    column_overrides: tuple[ColumnOverride, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _require_int(value: Any, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{name} 必须是 >= {minimum} 的整数")
    return value


def _require_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} 必须是 0 到 1 之间的数值")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ConfigError(f"{name} 必须是 0 到 1 之间的数值")
    return number


def _require_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} 必须是非空字符串")
    return value.strip()


def _require_int_value(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} 必须是整数")
    return value


def validate_column_overrides(raw: dict[str, Any]) -> None:
    """校验可选的 ``column_overrides`` 段；缺省时完全不做任何检查。"""

    if "column_overrides" not in raw:
        return
    rules = raw["column_overrides"]
    if rules is None:
        # 只写了一个键、内容全部注释掉时视为「没有规则」，不报错。
        return
    if not isinstance(rules, list):
        raise ConfigError("column_overrides 必须是列表")
    for position, rule in enumerate(rules):
        label = f"column_overrides[{position}]"
        if not isinstance(rule, dict):
            raise ConfigError(f"{label} 必须是映射")
        extra = set(rule) - _OVERRIDE_KEYS
        if extra:
            raise ConfigError(f"{label} 存在未知配置项: {', '.join(sorted(extra))}")
        source_glob = rule.get("source_glob", "*")
        if not isinstance(source_glob, str) or not source_glob.strip():
            raise ConfigError(f"{label}.source_glob 必须是非空字符串")
        has_regex = "column_name_regex" in rule
        has_index = "column_index" in rule
        if not has_regex and not has_index:
            raise ConfigError(
                f"{label} 必须至少提供 column_name_regex 或 column_index"
            )
        if has_regex:
            regex = rule["column_name_regex"]
            if not isinstance(regex, str) or not regex:
                raise ConfigError(f"{label}.column_name_regex 必须是非空字符串")
            try:
                re.compile(regex)
            except re.error as exc:
                raise ConfigError(
                    f"{label}.column_name_regex 非法: {exc}"
                ) from exc
        if has_index:
            index = rule["column_index"]
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ConfigError(f"{label}.column_index 必须是 >= 0 的整数")
        if "name" not in rule and "type" not in rule:
            raise ConfigError(f"{label} 必须至少提供 name 或 type")
        if "name" in rule:
            _require_str(rule["name"], f"{label}.name")
        if "type" in rule and rule["type"] not in COLUMN_TYPE_ORDER:
            raise ConfigError(
                f"{label}.type 只能是 {', '.join(COLUMN_TYPE_ORDER)} 之一"
            )
        if "priority" in rule:
            _require_int_value(rule["priority"], f"{label}.priority")


def _build_column_overrides(raw: dict[str, Any]) -> tuple[ColumnOverride, ...]:
    rules: list[ColumnOverride] = []
    for position, rule in enumerate(raw.get("column_overrides") or []):
        name = rule.get("name")
        rules.append(
            ColumnOverride(
                source_glob=str(rule.get("source_glob", "*")).strip(),
                column_name_regex=rule.get("column_name_regex"),
                column_index=rule.get("column_index"),
                name=None if name is None else str(name).strip(),
                type=rule.get("type"),
                priority=int(rule.get("priority", 0)),
                order=position,
            )
        )
    return tuple(rules)


def validate_config(raw: dict[str, Any]) -> None:
    """校验合并后的配置，尽早暴露拼写错误和非法取值。"""

    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"未知顶层配置项: {', '.join(sorted(unknown))}")
    for section, allowed in (
        ("table_selector", _TABLE_KEYS),
        ("inference", _INFERENCE_KEYS),
        ("normalization", _NORMALIZATION_KEYS),
    ):
        value = raw.get(section)
        if not isinstance(value, dict):
            raise ConfigError(f"{section} 必须是映射")
        extra = set(value) - allowed
        if extra:
            raise ConfigError(f"{section} 存在未知配置项: {', '.join(sorted(extra))}")

    for key in (
        "project_name",
        "input_dir",
        "file_glob",
        "output_dir",
        "database",
        "evidence_dir",
        "evidence_index",
        "report_md",
        "report_json",
    ):
        _require_str(raw.get(key), key)

    selector = raw["table_selector"]
    _require_int(selector["min_rows"], "table_selector.min_rows", 0)
    _require_int(selector["min_cols"], "table_selector.min_cols", 1)
    if not isinstance(selector["require_header"], bool):
        raise ConfigError("table_selector.require_header 必须是布尔值")
    _require_str(selector["header_hint_regex"], "table_selector.header_hint_regex")
    try:
        re.compile(selector["header_hint_regex"])
    except re.error as exc:
        raise ConfigError(f"header_hint_regex 非法: {exc}") from exc
    if selector["nested_table_policy"] not in {"skip", "flatten"}:
        raise ConfigError("table_selector.nested_table_policy 只能是 skip 或 flatten")

    inference = raw["inference"]
    _require_float(inference["min_match_ratio"], "inference.min_match_ratio")
    _require_float(inference["min_non_empty_ratio"], "inference.min_non_empty_ratio")
    _require_int(
        inference["max_enum_cardinality"], "inference.max_enum_cardinality", 1
    )
    _require_int(inference["max_sample_values"], "inference.max_sample_values", 1)
    _require_str(inference["identifier_pattern"], "inference.identifier_pattern")
    try:
        re.compile(inference["identifier_pattern"])
    except re.error as exc:
        raise ConfigError(f"identifier_pattern 非法: {exc}") from exc

    formats = raw["normalization"]["date_formats"]
    if not isinstance(formats, list) or not formats:
        raise ConfigError("normalization.date_formats 必须是非空列表")
    if any(not isinstance(item, str) or not item for item in formats):
        raise ConfigError("normalization.date_formats 中的每一项都必须是非空字符串")

    validate_column_overrides(raw)


def _canonical_sha256(raw: dict[str, Any]) -> str:
    payload = yaml.safe_dump(
        raw, allow_unicode=True, sort_keys=True, default_flow_style=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_config(path: str | Path) -> AppConfig:
    """加载 YAML，与默认值深合并，校验后返回不可变配置对象。"""

    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"配置文件不存在: {config_path}")
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"无法读取配置: {exc}") from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ConfigError("配置文件顶层必须是映射")
    raw = _deep_merge(DEFAULT_CONFIG, loaded)
    validate_config(raw)

    selector_raw = raw["table_selector"]
    inference_raw = raw["inference"]
    normalization_raw = raw["normalization"]
    selector = TableSelector(
        min_rows=selector_raw["min_rows"],
        min_cols=selector_raw["min_cols"],
        require_header=selector_raw["require_header"],
        header_hint_regex=selector_raw["header_hint_regex"],
        nested_table_policy=selector_raw["nested_table_policy"],
    )
    inference = InferenceOptions(
        min_match_ratio=float(inference_raw["min_match_ratio"]),
        min_non_empty_ratio=float(inference_raw["min_non_empty_ratio"]),
        max_enum_cardinality=inference_raw["max_enum_cardinality"],
        max_sample_values=inference_raw["max_sample_values"],
        identifier_pattern=inference_raw["identifier_pattern"],
    )
    normalization = NormalizationOptions(
        date_formats=tuple(normalization_raw["date_formats"])
    )
    return AppConfig(
        project_name=raw["project_name"],
        input_dir=Path(raw["input_dir"]),
        file_glob=raw["file_glob"],
        output_dir=Path(raw["output_dir"]),
        database=Path(raw["database"]),
        evidence_dir=Path(raw["evidence_dir"]),
        evidence_index=Path(raw["evidence_index"]),
        report_md=Path(raw["report_md"]),
        report_json=Path(raw["report_json"]),
        table_selector=selector,
        inference=inference,
        normalization=normalization,
        column_overrides=_build_column_overrides(raw),
        config_sha256=_canonical_sha256(raw),
        source_path=config_path,
        raw=copy.deepcopy(raw),
    )
