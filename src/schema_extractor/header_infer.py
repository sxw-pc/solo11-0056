"""列语义推断：只用确定性规则，不做机器学习。"""

from __future__ import annotations

from dataclasses import dataclass

from .config import InferenceOptions, NormalizationOptions
from .normalize import (
    DATE_PRECISIONS,
    clean_text,
    date_precision_of,
    is_currency,
    is_decimal,
    is_integer,
    is_percent,
)

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
    date_precisions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "type": self.type,
            "match_ratio": self.match_ratio,
            "sample_values": list(self.sample_values),
            "unnamed": self.unnamed,
        }
        # 只记录非整日精度（day 为基准精度），无则省略以保持输出稳定。
        if self.date_precisions:
            payload["date_precisions"] = list(self.date_precisions)
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
    partial = normalization.partial_date_formats
    time_formats = normalization.time_formats
    date_hits: list[str] = []
    for value in non_empty:
        precision = date_precision_of(
            value,
            formats,
            partial_date_formats=partial,
            time_formats=time_formats,
        )
        if precision is not None:
            date_hits.append(precision)
    checks: list[tuple[str, int]] = [
        ("date", len(date_hits)),
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
            date_precisions: tuple[str, ...] = ()
            if type_name == "date":
                non_day = {hit for hit in date_hits if hit != "day"}
                date_precisions = tuple(
                    precision for precision in DATE_PRECISIONS if precision in non_day
                )
            return ColumnProfile(
                name, type_name, ratio, samples, unnamed, date_precisions
            )
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
