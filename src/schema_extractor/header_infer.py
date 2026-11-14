"""列语义推断：只用确定性规则，不做机器学习。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence

from .config import (
    COLUMN_TYPE_ORDER,
    ColumnOverride,
    InferenceOptions,
    NormalizationOptions,
)
from .normalize import clean_text, is_currency, is_date, is_decimal, is_integer, is_percent

TYPE_ORDER = COLUMN_TYPE_ORDER


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    type: str
    match_ratio: float
    sample_values: list[str]
    unnamed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.type,
            "match_ratio": self.match_ratio,
            "sample_values": list(self.sample_values),
            "unnamed": self.unnamed,
        }


def _ratio(matches: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(matches / total, 4)


def _enum_like(values: list[str], options: InferenceOptions) -> bool:
    distinct = len(set(values))
    if distinct > options.max_enum_cardinality:
        return False
    return distinct < len(values) or len(values) <= 2


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
    for type_name, matches in checks:
        ratio = _ratio(matches, len(non_empty))
        if ratio >= options.min_match_ratio:
            return ColumnProfile(name, type_name, ratio, samples, unnamed)
    if _enum_like(non_empty, options):
        return ColumnProfile(name, "enum", 1.0, samples, unnamed)
    return ColumnProfile(name, "text", non_empty_ratio, samples, unnamed)


def select_column_override(
    rules: Sequence[ColumnOverride],
    source_file: str,
    column_name: str,
    col_index: int,
) -> tuple[ColumnOverride | None, ColumnOverride | None]:
    """为某一列消解多条命中规则，返回 ``(命名的规则, 定类型的规则)``。

    列名与类型**分别**消解：只有声明了 ``name`` 的规则参与列名竞争，
    只有声明了 ``type`` 的规则参与类型竞争。这样一条「只改名」与一条
    「只定类型」的规则可以同时生效。

    每项竞争取 ``(priority, order)`` 最大者：``priority`` 数值大者优先，
    同优先级时配置中靠后的规则胜出。``order`` 是规则在 YAML 列表中的
    原始位置，与任何运行期状态无关，因此结果稳定、可解释。
    """
    matched = [
        rule for rule in rules if rule.matches(source_file, column_name, col_index)
    ]
    named = max(
        (rule for rule in matched if rule.name is not None),
        key=lambda rule: (rule.priority, rule.order),
        default=None,
    )
    typed = max(
        (rule for rule in matched if rule.type is not None),
        key=lambda rule: (rule.priority, rule.order),
        default=None,
    )
    return named, typed


def _forced_match_ratio(
    values: list[str],
    type_name: str,
    options: InferenceOptions,
    normalization: NormalizationOptions,
) -> float:
    """按强制类型重算匹配比例：符合该类型的非空值占比。

    ``text`` / ``enum`` 对任何非空值都成立，因此有非空值时为 1.0；
    没有非空值时为 0.0。该值只用于台账与报告展示，不影响逐格归一化。
    """
    non_empty = [clean_text(value) for value in values if clean_text(value)]
    if not non_empty:
        return 0.0
    formats = normalization.date_formats
    checkers: dict[str, Any] = {
        "identifier": lambda value: options.identifier_re.fullmatch(value) is not None,
        "currency": is_currency,
        "percent": is_percent,
        "integer": is_integer,
        "decimal": lambda value: is_decimal(value) or is_integer(value),
        "date": lambda value: is_date(value, formats),
        "enum": lambda value: True,
        "text": lambda value: True,
    }
    checker = checkers[type_name]
    return _ratio(sum(1 for value in non_empty if checker(value)), len(non_empty))


def apply_column_overrides(
    profiles: list[ColumnProfile],
    rows: list[list[str | None]],
    rules: Sequence[ColumnOverride],
    source_file: str,
    options: InferenceOptions,
    normalization: NormalizationOptions,
) -> tuple[list[ColumnProfile], list[dict[str, Any]]]:
    """在推断完成后应用覆盖规则。

    返回 ``(新列列表, 命中记录)``。命中记录按列序保存每条生效规则的
    ``col_index`` / ``name`` / ``column_name_regex`` / ``priority`` 及
    覆盖前后的名字与类型，供报告展示；该记录只用于诊断，不参与抽取。

    未配置规则或规则未命中任何列时，返回的列与输入逐字段相同（含对象
    身份），因此不写 ``column_overrides`` 的输出与旧版本完全一致。
    """
    if not rules:
        return profiles, []
    applied: list[dict[str, Any]] = []
    adjusted: list[ColumnProfile] = []
    for index, profile in enumerate(profiles):
        matched = [
            rule
            for rule in rules
            if rule.matches(source_file, profile.name, index)
        ]
        named, typed = select_column_override(
            rules, source_file, profile.name, index
        )
        if named is None and typed is None:
            adjusted.append(profile)
            continue
        name = profile.name if named is None else str(named.name)
        type_name = profile.type if typed is None else str(typed.type)
        match_ratio = profile.match_ratio
        if typed is not None:
            match_ratio = _forced_match_ratio(
                _column_values(rows, index), type_name, options, normalization
            )
        unnamed = profile.unnamed and named is None
        adjusted.append(
            replace(
                profile,
                name=name,
                type=type_name,
                match_ratio=match_ratio,
                unnamed=unnamed,
            )
        )
        applied.append(
            {
                "col_index": index,
                "original_name": profile.name,
                "original_type": profile.type,
                "name": name,
                "requested_name": None if named is None else name,
                "type": type_name,
                # 所有命中本列的规则（含因优先级落选的），供诊断统计；
                # 使用可哈希的 selectors_key，避免在调用方重复排序比较。
                "matched_rules": [rule.selectors_key for rule in matched],
                "applied_name_rule": None if named is None else named.selectors_key,
                "applied_type_rule": None if typed is None else typed.selectors_key,
            }
        )
    return adjusted, applied


def _column_values(rows: list[list[str | None]], index: int) -> list[str]:
    return [
        "" if index >= len(row) or row[index] is None else str(row[index])
        for row in rows
    ]


def infer_columns(
    headers: list[str],
    unnamed_headers: list[bool],
    rows: list[list[str | None]],
    options: InferenceOptions,
    normalization: NormalizationOptions,
) -> list[ColumnProfile]:
    profiles: list[ColumnProfile] = []
    for index, header in enumerate(headers):
        values = _column_values(rows, index)
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
