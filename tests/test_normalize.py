from schema_extractor.normalize import (
    DEFAULT_DATE_FORMATS,
    canonical_decimal,
    clean_text,
    date_precision_of,
    is_currency,
    is_date,
    is_decimal,
    is_integer,
    is_percent,
    normalize_cell,
    parse_currency,
    parse_date,
    parse_date_precise,
    parse_percent,
    parse_plain_number,
)


def test_clean_text_collapses_all_whitespace():
    assert clean_text("  示例\t项目\n  甲  ") == "示例 项目 甲"
    assert clean_text(" 甲 乙 ") == "甲 乙"
    assert clean_text(None) == ""


def test_currency_parsing_and_units():
    assert parse_currency("¥1,234.50") is not None
    number, unit = parse_currency("¥1,234.50")
    assert canonical_decimal(number) == "1234.5"
    assert unit == "CNY"
    assert parse_currency("$1,000")[1] == "USD"
    assert parse_currency("300元")[1] == "CNY"
    assert parse_currency("100") is None
    assert is_currency("￥12") is True
    assert is_currency("12") is False


def test_percent_parsing():
    assert canonical_decimal(parse_percent("12.5%")) == "12.5"
    assert canonical_decimal(parse_percent("-2.5%")) == "-2.5"
    assert parse_percent("12") is None
    assert is_percent("0%") is True


def test_plain_numbers():
    assert parse_plain_number("1,234")[1] == "integer"
    assert parse_plain_number("1,234.50")[1] == "decimal"
    assert parse_plain_number("1,23") is None
    assert is_integer("1,000") is True
    assert is_decimal("1.5") is True
    assert is_decimal("1,000") is False


def test_canonical_decimal_removes_trailing_zeroes():
    assert canonical_decimal(parse_plain_number("1,234.50")[0]) == "1234.5"
    assert canonical_decimal(parse_plain_number("-0.0")[0]) == "0"
    assert canonical_decimal(parse_plain_number("42.000")[0]) == "42"
    assert canonical_decimal(parse_plain_number("-3.25")[0]) == "-3.25"


def test_date_formats_and_invalid_values():
    assert parse_date("2026-01-02") == "2026-01-02"
    assert parse_date("2026/1/2") == "2026-01-02"
    assert parse_date("2026年1月2日") == "2026-01-02"
    assert parse_date("2026-13-40") is None
    assert parse_date("2026-01-02", DEFAULT_DATE_FORMATS) == "2026-01-02"
    assert is_date("2026/01/02") is True


def test_partial_precision_dates_keep_input_granularity():
    assert parse_date("2026") == "2026"
    assert parse_date("2026年") == "2026"
    assert parse_date("2026-01") == "2026-01"
    assert parse_date("2026/1") == "2026-01"
    assert parse_date("2026年1月") == "2026-01"
    # 部分精度不补零到整日
    assert parse_date("2026-01") != "2026-01-01"


def test_parse_date_precise_reports_precision():
    assert parse_date_precise("2026") == ("2026", "year")
    assert parse_date_precise("2026-01") == ("2026-01", "month")
    assert parse_date_precise("2026年1月") == ("2026-01", "month")
    assert parse_date_precise("2026-01-02") == ("2026-01-02", "day")
    assert parse_date_precise("不是日期") is None


def test_dates_with_time_of_day():
    assert parse_date_precise("2026-01-02 13:05") == ("2026-01-02T13:05", "minute")
    assert parse_date_precise("2026/01/03 08:00") == ("2026-01-03T08:00", "minute")
    assert parse_date_precise("2026年1月2日 8:00") == ("2026-01-02T08:00", "minute")
    assert parse_date_precise("2026-01-02T13:05") == ("2026-01-02T13:05", "minute")
    assert parse_date_precise("2026-01-02 13:05:09") == (
        "2026-01-02T13:05:09",
        "second",
    )
    # 时分不补秒
    assert parse_date("2026-01-02 13:05") == "2026-01-02T13:05"


def test_invalid_partial_and_time_values_still_fail():
    assert parse_date("2026-13") is None
    assert parse_date("2026-13-40") is None
    assert parse_date("2026-01-02 25:00") is None
    assert parse_date("2026-01-02 13:61") is None
    assert parse_date("2026-01-02 13:05:61") is None
    assert parse_date("2026-") is None
    assert parse_date("2026-01-") is None
    assert is_date("2026-13") is False


def test_bare_year_requires_exactly_four_digits():
    assert parse_date("2026") == "2026"
    assert parse_date("800") is None
    assert parse_date("42") is None
    assert parse_date("20266") is None


def test_date_precision_of_and_opt_out_options():
    assert date_precision_of("2026") == "year"
    assert date_precision_of("2026-01") == "month"
    assert date_precision_of("2026-01-02") == "day"
    assert date_precision_of("2026-01-02 13:05") == "minute"
    assert date_precision_of("不是日期") is None
    # 空列表可停用新形态，整日格式不受影响
    assert parse_date("2026-01", partial_date_formats=()) is None
    assert parse_date("2026-01-02 13:05", time_formats=()) is None
    assert parse_date("2026", partial_date_formats=()) is None
    assert parse_date("2026-01-02", partial_date_formats=(), time_formats=()) == (
        "2026-01-02"
    )


def test_normalize_cell_partial_date_and_time():
    assert normalize_cell("2026-01", "date") == ("2026-01", "", None)
    assert normalize_cell("2026", "date") == ("2026", "", None)
    assert normalize_cell("2026-01-02 13:05", "date") == (
        "2026-01-02T13:05",
        "",
        None,
    )
    assert normalize_cell("2026-13", "date") == ("2026-13", "", "date_unparsable")


def test_normalize_cell_successes():
    formats = DEFAULT_DATE_FORMATS
    assert normalize_cell("1,234.50", "decimal", formats) == ("1234.5", "", None)
    assert normalize_cell("¥120", "currency", formats) == ("120", "CNY", None)
    assert normalize_cell("12.5%", "percent", formats) == ("12.5", "%", None)
    assert normalize_cell("2026/1/2", "date", formats) == ("2026-01-02", "", None)
    assert normalize_cell("  A-01 ", "identifier", formats) == ("A-01", "", None)


def test_normalize_cell_failures_do_not_raise():
    formats = DEFAULT_DATE_FORMATS
    assert normalize_cell("坏日期", "date", formats) == (
        "坏日期",
        "",
        "date_unparsable",
    )
    assert normalize_cell("十二", "integer", formats) == (
        "十二",
        "",
        "number_unparsable",
    )
    assert normalize_cell("十二%", "percent", formats) == (
        "十二%",
        "%",
        "percent_unparsable",
    )
    assert normalize_cell("一百元", "currency", formats) == (
        "一百元",
        "",
        "number_unparsable",
    )


def test_empty_cell_is_always_empty_string():
    assert normalize_cell(None, "date") == ("", "", None)
    assert normalize_cell("   ", "currency") == ("", "", None)
    assert normalize_cell("", "text") == ("", "", None)
