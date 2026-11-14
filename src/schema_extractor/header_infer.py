"""列语义推断：表头语义与取值证据协同判定，只用确定性规则，不做机器学习。

判定顺序（同一输入必然得到同一结果）：

1. 取值证据达到 ``min_match_ratio`` 且与表头提示一致（或没有表头提示）时，
   按取值判定，``type_source="value"``、``confidence="high"``。
2. 表头提示类型与取值强类型明显冲突（取值达到阈值且不在兼容集内，
   例如名为“日期”的列多数取值是货币）时，回退 ``text``，
   ``type_source="conflict_fallback"``、``confidence="low"``，由
   processing 层补一条 ``header_value_conflict`` issue，不抛异常。
3. 表头提示类型的取值比例落在 ``[HEADER_HINT_MIN_RATIO, min_match_ratio)``
   且取值侧没有冲突强类型时，采信表头类型，
   ``type_source="header"``、``confidence="low"``。
4. 表头提示几乎得不到取值支持（比例低于 ``HEADER_HINT_MIN_RATIO``）时，
   忽略提示，完全按取值判定——“真整数”不会因为列名被误判。

低置信标记只出现在新增字段 ``type_source`` / ``confidence`` 上；
默认路径下 ``ColumnProfile.to_dict()`` 的键集合与旧版本完全一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import InferenceOptions, NormalizationOptions
from .normalize import clean_text, is_currency, is_date, is_decimal, is_integer, is_percent

TYPE_ORDER = (
    "identifier",
    "currency",
    "percent",
    "integer",
    "decimal",
    "date",
    "enum",
    "text",
)

#: 表头提示被采信所需的最低取值匹配比例（含边界）。
#: 低于该值视为“表头几乎得不到取值支持”，提示被忽略。
HEADER_HINT_MIN_RATIO = 0.25

#: 类型来源：取值判定 / 采信表头 / 冲突回退。
TYPE_SOURCE_VALUE = "value"
TYPE_SOURCE_HEADER = "header"
TYPE_SOURCE_CONFLICT = "conflict_fallback"

#: 置信度标记。
CONFIDENCE_HIGH = "high"
CONFIDENCE_LOW = "low"

_ASCII_WORD = r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])"


def _compile_hint(chinese: tuple[str, ...], english: tuple[str, ...]) -> re.Pattern[str]:
    parts = [re.escape(word) for word in chinese]
    parts.extend(_ASCII_WORD.format(re.escape(word)) for word in english)
    return re.compile("|".join(parts), re.IGNORECASE)


#: 表头提示词表：按类型优先级排列，命中即定，顺序即冲突时的裁决顺序。
_HINT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("date", _compile_hint(("日期", "时间"), ("date", "time"))),
    (
        "currency",
        _compile_hint(
            ("金额", "费用", "价格", "单价", "总价", "价款"),
            ("amount", "price", "cost"),
        ),
    ),
    (
        "percent",
        _compile_hint(("比例", "比率", "占比", "率"), ("percent", "rate", "ratio")),
    ),
    ("identifier", _compile_hint(("编号", "代码"), ("id", "code"))),
)

#: 表头提示类型所兼容的取值强类型：取值落在兼容集内时不算冲突，
#: 表头提示可以在有少量直接证据时采信（例如金额列里的纯数字）。
_COMPATIBLE_WITH_HINT: dict[str, frozenset[str]] = {
    "date": frozenset(),
    "currency": frozenset({"integer", "decimal"}),
    "percent": frozenset({"integer", "decimal"}),
    "identifier": frozenset({"integer"}),
}


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    type: str
    match_ratio: float
    sample_values: list[str]
    unnamed: bool
    type_source: str = TYPE_SOURCE_VALUE
    confidence: str = CONFIDENCE_HIGH

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "type": self.type,
            "match_ratio": self.match_ratio,
            "sample_values": list(self.sample_values),
            "unnamed": self.unnamed,
        }
        if (
            self.type_source != TYPE_SOURCE_VALUE
            or self.confidence != CONFIDENCE_HIGH
        ):
            payload["type_source"] = self.type_source
            payload["confidence"] = self.confidence
        return payload


def _ratio(matches: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(matches / total, 4)


def _enum_like(values: list[str], options: InferenceOptions) -> bool:
    distinct = len(set(values))
    if distinct > options.max_enum_cardinality:
        return False
    return distinct < len(values) or len(values) <= 2


def _header_hint_type(name: str, unnamed: bool) -> str | None:
    """从列名提取语义提示；合成列名（unnamed）不参与提示。"""
    if unnamed:
        return None
    text = clean_text(name)
    if not text:
        return None
    for type_name, pattern in _HINT_RULES:
        if pattern.search(text):
            return type_name
    return None


def infer_column(
    name: str,
    values: list[str],
    unnamed: bool,
    options: InferenceOptions,
    normalization: NormalizationOptions,
) -> ColumnProfile:
    """按固定优先级推断一列类型；阈值不满足时回退到 text。"""
    non_empty = [clean_text(value) for value in values if clean_text(value)]
    total = len(values)
    samples = non_empty[: options.max_sample_values]
    if not non_empty:
        return ColumnProfile(name, "text", 0.0, [], unnamed)
    non_empty_ratio = _ratio(len(non_empty), total)
    if non_empty_ratio < options.min_non_empty_ratio:
        return ColumnProfile(name, "text", non_empty_ratio, samples, unnamed)

    formats = normalization.date_formats
    checks: list[tuple[str, int]] = [
        ("date", sum(1 for value in non_empty if is_date(value, formats))),
        ("percent", sum(1 for value in non_empty if is_percent(value))),
        ("currency", sum(1 for value in non_empty if is_currency(value))),
        ("integer", sum(1 for value in non_empty if is_integer(value))),
        (
            "decimal",
            sum(
                1
                for value in non_empty
                if is_decimal(value) or is_integer(value)
            ),
        ),
        (
            "identifier",
            sum(
                1
                for value in non_empty
                if options.identifier_re.fullmatch(value) is not None
            ),
        ),
    ]
    ratios = {
        type_name: _ratio(matches, len(non_empty)) for type_name, matches in checks
    }
    value_type = next(
        (
            type_name
            for type_name, _ in checks
            if ratios[type_name] >= options.min_match_ratio
        ),
        None,
    )

    hint = _header_hint_type(name, unnamed)
    if hint is not None and hint != value_type:
        if value_type is not None and value_type not in _COMPATIBLE_WITH_HINT[hint]:
            return ColumnProfile(
                name,
                "text",
                non_empty_ratio,
                samples,
                unnamed,
                type_source=TYPE_SOURCE_CONFLICT,
                confidence=CONFIDENCE_LOW,
            )
        if ratios[hint] >= HEADER_HINT_MIN_RATIO:
            return ColumnProfile(
                name,
                hint,
                ratios[hint],
                samples,
                unnamed,
                type_source=TYPE_SOURCE_HEADER,
                confidence=CONFIDENCE_LOW,
            )
    if value_type is not None:
        return ColumnProfile(name, value_type, ratios[value_type], samples, unnamed)
    if _enum_like(non_empty, options):
        return ColumnProfile(name, "enum", 1.0, samples, unnamed)
    return ColumnProfile(name, "text", non_empty_ratio, samples, unnamed)


def infer_columns(
    headers: list[str],
    unnamed_headers: list[bool],
    rows: list[list[str | None]],
    options: InferenceOptions,
    normalization: NormalizationOptions,
) -> list[ColumnProfile]:
    profiles: list[ColumnProfile] = []
    for index, header in enumerate(headers):
        values = [
            "" if index >= len(row) or row[index] is None else str(row[index])
            for row in rows
        ]
        unnamed = unnamed_headers[index] if index < len(unnamed_headers) else True
        profiles.append(
            infer_column(header, values, unnamed, options, normalization)
        )
    return profiles


def type_distribution(profiles: list[ColumnProfile]) -> dict[str, int]:
    distribution = {type_name: 0 for type_name in TYPE_ORDER}
    for profile in profiles:
        distribution[profile.type] = distribution.get(profile.type, 0) + 1
    return {key: value for key, value in distribution.items() if value}
