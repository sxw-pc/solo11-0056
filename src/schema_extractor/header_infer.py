"""列语义推断：只用确定性规则，不做机器学习。"""

from __future__ import annotations

from dataclasses import dataclass

from .config import InferenceOptions, NormalizationOptions
from .normalize import clean_text, is_currency, is_date, is_decimal, is_integer, is_percent
from .trace import BranchCheck, ColumnTrace

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


def _infer_column_with_trace(
    name: str,
    values: list[str],
    unnamed: bool,
    options: InferenceOptions,
    normalization: NormalizationOptions,
    column_index: int,
) -> tuple[ColumnProfile, ColumnTrace]:
    """推断一列并同时记录命中分支；判定逻辑与 ``infer_column`` 完全一致。"""
    non_empty = [clean_text(value) for value in values if clean_text(value)]
    total = len(values)
    samples = non_empty[: options.max_sample_values]
    truncated = len(non_empty) > len(samples)

    def trace(
        branch: str,
        branch_kind: str,
        priority_index: int | None,
        match_ratio: float,
        ratio_scope: str,
        matched: int | None,
        compared: int | None,
        checks: list[BranchCheck],
    ) -> ColumnTrace:
        return ColumnTrace(
            column_index=column_index,
            name=name,
            unnamed=unnamed,
            type=branch,
            branch=branch,
            branch_kind=branch_kind,
            priority_index=priority_index,
            match_ratio=match_ratio,
            ratio_scope=ratio_scope,
            matched=matched,
            compared=compared,
            non_empty_ratio=_ratio(len(non_empty), total),
            non_empty=len(non_empty),
            total=total,
            distinct_count=len(set(non_empty)),
            sample_values=list(samples),
            samples_truncated=truncated,
            checks=checks,
        )

    if not non_empty:
        profile = ColumnProfile(name, "text", 0.0, [], unnamed)
        return profile, trace("text", "empty", None, 0.0, "total", 0, total, [])
    non_empty_ratio = _ratio(len(non_empty), total)
    if non_empty_ratio < options.min_non_empty_ratio:
        profile = ColumnProfile(name, "text", non_empty_ratio, samples, unnamed)
        return profile, trace(
            "text",
            "sparse",
            None,
            non_empty_ratio,
            "total",
            len(non_empty),
            total,
            [],
        )

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
    evaluated: list[BranchCheck] = []
    hit_index: int | None = None
    hit_ratio = 0.0
    for branch_index, (type_name, matches) in enumerate(checks):
        ratio = _ratio(matches, len(non_empty))
        passed = ratio >= options.min_match_ratio
        evaluated.append(
            BranchCheck(
                branch=type_name,
                matches=matches,
                ratio=ratio,
                threshold=options.min_match_ratio,
                passed=passed,
            )
        )
        if passed:
            hit_index = branch_index
            hit_ratio = ratio
            break
    if hit_index is not None:
        type_name = checks[hit_index][0]
        profile = ColumnProfile(name, type_name, hit_ratio, samples, unnamed)
        return profile, trace(
            type_name,
            "priority",
            hit_index,
            hit_ratio,
            "non_empty",
            checks[hit_index][1],
            len(non_empty),
            evaluated,
        )
    if _enum_like(non_empty, options):
        profile = ColumnProfile(name, "enum", 1.0, samples, unnamed)
        return profile, trace(
            "enum", "enum", None, 1.0, "non_empty", None, len(non_empty), evaluated
        )
    profile = ColumnProfile(name, "text", non_empty_ratio, samples, unnamed)
    return profile, trace(
        "text",
        "fallback",
        None,
        non_empty_ratio,
        "total",
        len(non_empty),
        total,
        evaluated,
    )


def infer_column(
    name: str,
    values: list[str],
    unnamed: bool,
    options: InferenceOptions,
    normalization: NormalizationOptions,
) -> ColumnProfile:
    """按固定优先级推断一列类型；阈值不满足时回退到 text。"""
    profile, _ = _infer_column_with_trace(
        name, values, unnamed, options, normalization, 0
    )
    return profile


def infer_columns(
    headers: list[str],
    unnamed_headers: list[bool],
    rows: list[list[str | None]],
    options: InferenceOptions,
    normalization: NormalizationOptions,
) -> list[ColumnProfile]:
    profiles, _ = infer_columns_with_trace(
        headers, unnamed_headers, rows, options, normalization
    )
    return profiles


def infer_columns_with_trace(
    headers: list[str],
    unnamed_headers: list[bool],
    rows: list[list[str | None]],
    options: InferenceOptions,
    normalization: NormalizationOptions,
) -> tuple[list[ColumnProfile], list[ColumnTrace]]:
    """推断所有列，并按列顺序返回对应的追踪。"""
    profiles: list[ColumnProfile] = []
    traces: list[ColumnTrace] = []
    for index, header in enumerate(headers):
        values = [
            "" if index >= len(row) or row[index] is None else str(row[index])
            for row in rows
        ]
        unnamed = unnamed_headers[index] if index < len(unnamed_headers) else True
        profile, column_trace = _infer_column_with_trace(
            header, values, unnamed, options, normalization, index
        )
        profiles.append(profile)
        traces.append(column_trace)
    return profiles, traces


def type_distribution(profiles: list[ColumnProfile]) -> dict[str, int]:
    distribution = {type_name: 0 for type_name in TYPE_ORDER}
    for profile in profiles:
        distribution[profile.type] = distribution.get(profile.type, 0) + 1
    return {key: value for key, value in distribution.items() if value}
