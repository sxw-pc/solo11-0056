"""确定性单元格规范化与类型探测。

规范化失败时返回原文与 issue reason，不向上抛异常。

数字/货币/百分比共用一套符号语法（均为 ASCII 写法）：

- 前缀符号：``-¥1,200``、``+5``、``-2.5%``；
- 括号包裹：``(1,234.50)``、``(¥300)``、``(5%)``，整体表示负数；
- 后缀负号：``1234.5-``、``5%-``；
- 货币前缀之后还可以再带一个符号：``￥-300``。

一个值最多携带一个符号/括号指示，括号内不允许再出现括号；
``--5``、``(-5)``、``(+5)``、``-5-`` 之类的伪负数一律判为不合法。
纯括号文本（如 ``(备注)``）没有数字核心，不会被当作数字，
因此整列括号备注不会被误判成数值列。
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

DEFAULT_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日")

# 带符号/括号痕迹但解析失败时使用的 reason，与现有
# number_unparsable / percent_unparsable / date_unparsable 区分开，便于定位。
ACCOUNTING_NEGATIVE_UNPARSABLE = "accounting_negative_unparsable"

_WS = re.compile(r"\s+")
_DIGIT_RE = re.compile(r"\d")
_SIGN_HINT_RE = re.compile(r"[()+-]")
_CORE = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_BODY_RE = re.compile(
    r"^(?:(?P<prefix>[￥¥$€])\s*)?"
    r"(?P<inner_sign>[+-]?)"
    rf"(?P<core>{_CORE})"
    r"\s*(?P<suffix>元|人民币|CNY|USD|EUR)?"
    r"\s*(?P<percent>%?)$"
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


def _split_sign(text: str) -> tuple[str, bool, bool] | None:
    """剥离最外层符号/括号指示。

    返回 (剩余文本, 是否负数, 是否已消耗一个指示)；
    括号不成对、为空或嵌套时返回 None。
    """
    if text.startswith("(") or text.endswith(")"):
        if len(text) > 2 and text.startswith("(") and text.endswith(")"):
            inner = text[1:-1].strip()
            if "(" in inner or ")" in inner:
                return None
            return inner, True, True
        return None
    if text.endswith("-"):
        return text[:-1].strip(), True, True
    if text.startswith("-"):
        return text[1:].strip(), True, True
    if text.startswith("+"):
        return text[1:].strip(), False, True
    return text, False, False


def _parse_numeric(text: str) -> tuple[Decimal, str | None, bool, str] | None:
    """解析带可选符号、货币前后缀或百分号的数字。

    成功时返回 (数值, 货币单位或 None, 是否百分比, 数字核心文本)；
    不合法写法（含伪负数）返回 None。
    """
    parts = _split_sign(text)
    if parts is None:
        return None
    body, negative, sign_used = parts
    match = _BODY_RE.fullmatch(body)
    if match is None:
        return None
    inner_sign = match.group("inner_sign")
    if inner_sign:
        if sign_used:
            return None  # 双重指示：--5、(-5)、(¥-5) 等伪负数
        negative = inner_sign == "-"
    prefix = match.group("prefix")
    suffix = match.group("suffix")
    is_percent = bool(match.group("percent"))
    if is_percent and (prefix or suffix):
        return None  # 货币与百分号不混用
    unit = _CURRENCY_UNITS.get(prefix or suffix or "")
    number = _to_decimal(match.group("core"))
    if number is None:
        return None
    if negative:
        number = -number
    return number, unit, is_percent, match.group("core")


def parse_plain_number(raw: str) -> tuple[Decimal, str] | None:
    parsed = _parse_numeric(clean_text(raw))
    if parsed is None:
        return None
    number, unit, is_percent, core = parsed
    if unit is not None or is_percent:
        return None
    kind = "decimal" if "." in core else "integer"
    return number, kind


def parse_percent(raw: str) -> Decimal | None:
    parsed = _parse_numeric(clean_text(raw))
    if parsed is None:
        return None
    number, unit, is_percent, _core = parsed
    if not is_percent or unit is not None:
        return None
    return number


def parse_currency(raw: str) -> tuple[Decimal, str] | None:
    parsed = _parse_numeric(clean_text(raw))
    if parsed is None:
        return None
    number, unit, is_percent, _core = parsed
    if unit is None or is_percent:
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


def _number_failure_reason(text: str, default: str) -> str:
    """含数字且带符号/括号痕迹的单元格给出更好定位的 reason。"""
    if _DIGIT_RE.search(text) and _SIGN_HINT_RE.search(text):
        return ACCOUNTING_NEGATIVE_UNPARSABLE
    return default


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
            return text, "", _number_failure_reason(text, "number_unparsable")
        return canonical_decimal(parsed[0]), "", None
    if type_name == "percent":
        number = parse_percent(text)
        if number is None:
            return text, "%", _number_failure_reason(text, "percent_unparsable")
        return canonical_decimal(number), "%", None
    if type_name == "currency":
        parsed = parse_currency(text)
        if parsed is None:
            return text, "", _number_failure_reason(text, "number_unparsable")
        return canonical_decimal(parsed[0]), parsed[1], None
    if type_name == "date":
        normalized = parse_date(text, date_formats)
        if normalized is None:
            return text, "", "date_unparsable"
        return normalized, "", None
    return text, "", None
