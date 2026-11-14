"""确定性单元格规范化与类型探测。

规范化失败时返回原文与 issue reason，不向上抛异常。
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

DEFAULT_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日")
DEFAULT_PARTIAL_DATE_FORMATS = ("%Y-%m", "%Y/%m", "%Y年%m月", "%Y", "%Y年")
DEFAULT_TIME_FORMATS = ("%H:%M:%S", "%H:%M")

# 日期规范值的粒度：年、年月、年月日、年月日时分、年月日时分秒。
DATE_PRECISIONS = ("year", "month", "day", "minute", "second")

_WS = re.compile(r"\s+")
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_INTEGER_RE = re.compile(rf"^[+-]?(?:\d{{1,3}}(?:,\d{{3}})+|\d+)$")
_DECIMAL_RE = re.compile(rf"^[+-]?(?:\d{{1,3}}(?:,\d{{3}})+|\d+)\.\d+$")
_PERCENT_RE = re.compile(rf"^(?P<number>{_NUMBER})\s*%$")
_CURRENCY_RE = re.compile(
    rf"^(?:(?P<prefix>￥|¥|\$|€)\s*)?(?P<number>{_NUMBER})"
    r"\s*(?P<suffix>元|人民币|CNY|USD|EUR)?$"
)
_CURRENCY_UNITS = {
    "￥": "CNY",
    "¥": "CNY",
    "元": "CNY",
    "人民币": "CNY",
    "CNY": "CNY",
    "$": "USD",
    "USD": "USD",
    "€": "EUR",
    "EUR": "EUR",
}


def clean_text(value: object) -> str:
    """统一空白：NBSP 转空格、合并连续空白并去掉首尾空白。"""
    if value is None:
        return ""
    text = (
        str(value)
        .replace(" ", " ")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    return _WS.sub(" ", text).strip()


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def canonical_decimal(value: Decimal) -> str:
    """输出无科学计数法、无多余尾零的稳定十进制字符串。"""
    if not value.is_finite():
        return str(value)
    if value == 0:
        return "0"
    if value == value.to_integral_value():
        return str(value.quantize(Decimal(1)))
    text = format(value, "f").rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def parse_plain_number(raw: str) -> tuple[Decimal, str] | None:
    text = clean_text(raw)
    if _INTEGER_RE.fullmatch(text):
        number = _to_decimal(text)
        return (number, "integer") if number is not None else None
    if _DECIMAL_RE.fullmatch(text):
        number = _to_decimal(text)
        return (number, "decimal") if number is not None else None
    return None


def parse_percent(raw: str) -> Decimal | None:
    match = _PERCENT_RE.fullmatch(clean_text(raw))
    return _to_decimal(match.group("number")) if match else None


def parse_currency(raw: str) -> tuple[Decimal, str] | None:
    match = _CURRENCY_RE.fullmatch(clean_text(raw))
    if not match:
        return None
    prefix = match.group("prefix")
    suffix = match.group("suffix")
    if not prefix and not suffix:
        return None
    unit = _CURRENCY_UNITS.get(prefix or suffix or "")
    number = _to_decimal(match.group("number"))
    if unit is None or number is None:
        return None
    return number, unit


_BARE_YEAR_FORMATS = frozenset({"%Y", "%y"})
_BARE_YEAR_RE = re.compile(r"\d{4}")


def date_format_precision(date_format: str) -> str:
    """由 strptime 指令推导精度；只识别 %Y/%m/%d/%H(%I)/%M/%S 这一族。"""
    if "%S" in date_format or "%f" in date_format:
        return "second"
    if any(directive in date_format for directive in ("%H", "%I", "%M")):
        return "minute"
    if "%d" in date_format:
        return "day"
    if "%m" in date_format:
        return "month"
    return "year"


def _render_precision(moment: datetime, precision: str) -> str:
    """按命中精度输出规范值，不向上补零（年不补月、分不补秒）。"""
    if precision == "year":
        return f"{moment.year:04d}"
    if precision == "month":
        return f"{moment.year:04d}-{moment.month:02d}"
    day = moment.date().isoformat()
    if precision == "day":
        return day
    if precision == "minute":
        return f"{day}T{moment.hour:02d}:{moment.minute:02d}"
    return f"{day}T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}"


def _date_candidates(
    formats: tuple[str, ...] | list[str],
    partial_date_formats: tuple[str, ...] | list[str],
    time_formats: tuple[str, ...] | list[str],
) -> list[tuple[str, str]]:
    """展开为 (格式, 精度) 候选序列，保持稳定顺序并去重。"""
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(date_format: str, precision: str) -> None:
        if date_format not in seen:
            seen.add(date_format)
            candidates.append((date_format, precision))

    for date_format in formats:
        add(date_format, date_format_precision(date_format))
    day_formats = [
        date_format
        for date_format in formats
        if date_format_precision(date_format) == "day"
    ]
    for day_format in day_formats:
        for time_format in time_formats:
            precision = date_format_precision(time_format)
            if precision not in {"minute", "second"}:
                continue
            for separator in (" ", "T"):
                add(f"{day_format}{separator}{time_format}", precision)
    for date_format in partial_date_formats:
        precision = date_format_precision(date_format)
        if precision in {"minute", "second"}:
            continue
        add(date_format, precision)
    return candidates


def parse_date_precise(
    raw: str,
    formats: tuple[str, ...] | list[str] = DEFAULT_DATE_FORMATS,
    *,
    partial_date_formats: tuple[str, ...] | list[str] = DEFAULT_PARTIAL_DATE_FORMATS,
    time_formats: tuple[str, ...] | list[str] = DEFAULT_TIME_FORMATS,
) -> tuple[str, str] | None:
    """按配置顺序尝试日期格式，成功时返回 (规范值, 精度)。

    依次尝试：整日格式（date_formats）、整日+时分秒组合、部分精度格式
    （partial_date_formats）。规范值按输入实际粒度输出，不补零到整日、
    不补时秒；精度为 DATE_PRECISIONS 之一。全部失败返回 None。
    """
    text = clean_text(raw)
    if not text:
        return None
    for date_format, precision in _date_candidates(
        formats, partial_date_formats, time_formats
    ):
        if date_format in _BARE_YEAR_FORMATS and _BARE_YEAR_RE.fullmatch(text) is None:
            continue
        try:
            moment = datetime.strptime(text, date_format)
        except ValueError:
            continue
        return _render_precision(moment, precision), precision
    return None


def parse_date(
    raw: str,
    formats: tuple[str, ...] | list[str] = DEFAULT_DATE_FORMATS,
    *,
    partial_date_formats: tuple[str, ...] | list[str] = DEFAULT_PARTIAL_DATE_FORMATS,
    time_formats: tuple[str, ...] | list[str] = DEFAULT_TIME_FORMATS,
) -> str | None:
    """按配置顺序尝试日期格式，成功时返回规范值（粒度与输入一致）。"""
    parsed = parse_date_precise(
        raw,
        formats,
        partial_date_formats=partial_date_formats,
        time_formats=time_formats,
    )
    return parsed[0] if parsed is not None else None


def date_precision_of(
    raw: str,
    formats: tuple[str, ...] | list[str] = DEFAULT_DATE_FORMATS,
    *,
    partial_date_formats: tuple[str, ...] | list[str] = DEFAULT_PARTIAL_DATE_FORMATS,
    time_formats: tuple[str, ...] | list[str] = DEFAULT_TIME_FORMATS,
) -> str | None:
    """返回命中格式的精度；无法解析时返回 None。"""
    parsed = parse_date_precise(
        raw,
        formats,
        partial_date_formats=partial_date_formats,
        time_formats=time_formats,
    )
    return parsed[1] if parsed is not None else None


def is_integer(raw: str) -> bool:
    parsed = parse_plain_number(raw)
    return parsed is not None and parsed[1] == "integer"


def is_decimal(raw: str) -> bool:
    parsed = parse_plain_number(raw)
    return parsed is not None and parsed[1] == "decimal"


def is_percent(raw: str) -> bool:
    return parse_percent(raw) is not None


def is_currency(raw: str) -> bool:
    return parse_currency(raw) is not None


def is_date(
    raw: str,
    formats: tuple[str, ...] | list[str] = DEFAULT_DATE_FORMATS,
    *,
    partial_date_formats: tuple[str, ...] | list[str] = DEFAULT_PARTIAL_DATE_FORMATS,
    time_formats: tuple[str, ...] | list[str] = DEFAULT_TIME_FORMATS,
) -> bool:
    return (
        parse_date_precise(
            raw,
            formats,
            partial_date_formats=partial_date_formats,
            time_formats=time_formats,
        )
        is not None
    )


def normalize_cell(
    value: object,
    type_name: str,
    date_formats: tuple[str, ...] | list[str] = DEFAULT_DATE_FORMATS,
    *,
    partial_date_formats: tuple[str, ...] | list[str] = DEFAULT_PARTIAL_DATE_FORMATS,
    time_formats: tuple[str, ...] | list[str] = DEFAULT_TIME_FORMATS,
) -> tuple[str, str, str | None]:
    """返回 (规范化值, 单位标注, 失败原因)。

    任何失败都保留清理后的原文并返回 reason，而不抛异常。
    """
    text = clean_text(value)
    if not text:
        return "", "", None
    if type_name in {"integer", "decimal"}:
        parsed = parse_plain_number(text)
        if parsed is None:
            return text, "", "number_unparsable"
        return canonical_decimal(parsed[0]), "", None
    if type_name == "percent":
        number = parse_percent(text)
        if number is None:
            return text, "%", "percent_unparsable"
        return canonical_decimal(number), "%", None
    if type_name == "currency":
        parsed = parse_currency(text)
        if parsed is None:
            return text, "", "number_unparsable"
        return canonical_decimal(parsed[0]), parsed[1], None
    if type_name == "date":
        normalized = parse_date(
            text,
            date_formats,
            partial_date_formats=partial_date_formats,
            time_formats=time_formats,
        )
        if normalized is None:
            return text, "", "date_unparsable"
        return normalized, "", None
    return text, "", None
