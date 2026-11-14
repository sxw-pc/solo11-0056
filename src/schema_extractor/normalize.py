"""确定性单元格规范化与类型探测。

规范化失败时返回原文与 issue reason，不向上抛异常。

区间值（如 ``100-200``、``¥300~¥500``、``5%~8%``）会被拆成 lower / upper
两个规范化数字，单位只在两端一致时才被采信：两端都无单位 -> ``""``，两端
同一单位 -> 该单位；一端有单位而另一端没有（如 ``100-300元``）视为单位
对不齐，保留清理后的原文并给出 issue。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

DEFAULT_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日")

#: ``values`` 中区间单元格的判别字段值。
INTERVAL_KIND = "interval"
#: 区间两端之间允许出现的连接符；``-`` 与 ``~`` 语义相同，都是非严格 from-to。
INTERVAL_CONNECTORS = ("-", "~", "～", "–", "—")
INTERVAL_UNIT_CONFLICT = "interval_unit_conflict"
INTERVAL_UNPARSABLE = "interval_unparsable"
INTERVAL_BOUNDS_REVERSED = "interval_bounds_reversed"

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


def _interval_endpoint_pattern(side: str) -> str:
    """单个区间端点：可选货币前缀 + 数字 + 可选百分号/货币后缀。"""
    return (
        rf"(?:(?P<{side}_prefix>￥|¥|\$|€)\s*)?"
        rf"(?P<{side}_number>{_NUMBER})"
        rf"\s*(?P<{side}_suffix>%|元|人民币|CNY|USD|EUR)?"
    )


_INTERVAL_RE = re.compile(
    "^"
    + _interval_endpoint_pattern("lower")
    + r"\s*(?:"
    + "|".join(re.escape(connector) for connector in INTERVAL_CONNECTORS)
    + r")\s*"
    + _interval_endpoint_pattern("upper")
    + "$"
)


@dataclass(frozen=True)
class IntervalValue:
    """区间端点的数值与两端一致的单位（无单位时为 ``""``）。"""

    lower: Decimal
    upper: Decimal
    unit: str


@dataclass(frozen=True)
class IntervalAnalysis:
    """区间识别结果。

    ``shaped`` 表示单元格具备“数字 连接符 数字”的形状；即便单位冲突或上下限
    颠倒也为 True，以便列类型探测仍将其识别为区间列，再由规范化给出 issue。
    """

    shaped: bool
    value: IntervalValue | None
    reason: str | None


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


def _endpoint_unit(prefix: str | None, suffix: str | None) -> tuple[str | None, bool]:
    """返回 (端点单位, 端点内部是否自相矛盾)。"""
    prefix_unit = _CURRENCY_UNITS.get(prefix or "")
    suffix_unit = "%" if suffix == "%" else _CURRENCY_UNITS.get(suffix or "")
    if prefix_unit and suffix_unit and prefix_unit != suffix_unit:
        return None, True
    return prefix_unit or suffix_unit, False


def analyze_interval(raw: str) -> IntervalAnalysis:
    """识别 ``lower <连接符> upper`` 形状并给出数值、单位与失败原因。

    - ``-``、``~``、``～``、``–``、``—`` 一律视为“从下限到上限”的闭区间连接符，
      不按减法解释；只有两端都是独立数字（可带货币/百分号）时才成立。
    - ``A-1001`` 这类以字母开头的编号不满足“两端都是数字”的形状，不会被拆开。
    - 两端单位规则：两端都无单位 -> ``""``；两端单位相同 -> 该单位；
      只有一端带单位、或两端单位不同 -> 单位冲突。
    """
    text = clean_text(raw)
    match = _INTERVAL_RE.fullmatch(text)
    if not match:
        return IntervalAnalysis(False, None, None)
    lower = _to_decimal(match.group("lower_number"))
    upper = _to_decimal(match.group("upper_number"))
    if lower is None or upper is None:
        return IntervalAnalysis(True, None, INTERVAL_UNPARSABLE)
    lower_unit, lower_conflict = _endpoint_unit(
        match.group("lower_prefix"), match.group("lower_suffix")
    )
    upper_unit, upper_conflict = _endpoint_unit(
        match.group("upper_prefix"), match.group("upper_suffix")
    )
    if lower_conflict or upper_conflict or lower_unit != upper_unit:
        return IntervalAnalysis(True, None, INTERVAL_UNIT_CONFLICT)
    if lower > upper:
        return IntervalAnalysis(True, None, INTERVAL_BOUNDS_REVERSED)
    return IntervalAnalysis(True, IntervalValue(lower, upper, lower_unit or ""), None)


def is_interval(raw: str) -> bool:
    """是否具备区间形状；单位冲突、上下限颠倒仍算区间列，由规范化给 issue。"""
    return analyze_interval(raw).shaped


def parse_interval(raw: str) -> IntervalValue | None:
    """成功时返回区间端点与单位；形状不成立或单位/顺序非法时返回 None。"""
    return analyze_interval(raw).value


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
) -> tuple[str | IntervalValue, str, str | None]:
    """返回 (规范化值, 单位标注, 失败原因)。

    标量类型返回字符串；``interval`` 类型成功时返回 :class:`IntervalValue`，
    由 :func:`schema_extractor.models.cell_value` 转成记录里的可序列化结构。
    任何失败都保留清理后的原文并返回 reason，而不抛异常。
    """
    text = clean_text(value)
    if not text:
        return "", "", None
    if type_name == "interval":
        analysis = analyze_interval(text)
        if analysis.value is None:
            return text, "", analysis.reason or INTERVAL_UNPARSABLE
        return analysis.value, analysis.value.unit, None
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
        normalized = parse_date(text, date_formats)
        if normalized is None:
            return text, "", "date_unparsable"
        return normalized, "", None
    return text, "", None
