"""确定性单元格规范化与类型探测。

规范化失败时返回原文与 issue reason，不向上抛异常。
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

DEFAULT_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日")

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


def parse_date(
    raw: str, formats: tuple[str, ...] | list[str] = DEFAULT_DATE_FORMATS
) -> str | None:
    """按配置顺序尝试日期格式，成功时返回 ISO 日期。"""
    text = clean_text(raw)
    if not text:
        return None
    for date_format in formats:
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue
    return None


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


def is_date(raw: str, formats: tuple[str, ...] | list[str] = DEFAULT_DATE_FORMATS) -> bool:
    return parse_date(raw, formats) is not None


def normalize_cell(
    value: object,
    type_name: str,
    date_formats: tuple[str, ...] | list[str] = DEFAULT_DATE_FORMATS,
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
        if parsed is not None:
            return canonical_decimal(parsed[0]), parsed[1], None
        plain = parse_plain_number(text)
        if plain is not None:
            # 货币列中的无符号数字按金额规范化，但不标注单位。
            return canonical_decimal(plain[0]), "", None
        return text, "", "number_unparsable"
    if type_name == "date":
        normalized = parse_date(text, date_formats)
        if normalized is None:
            return text, "", "date_unparsable"
        return normalized, "", None
    return text, "", None
