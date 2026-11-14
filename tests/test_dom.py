from pathlib import Path

from schema_extractor.dom import Node, parse_file, parse_html


def test_parse_and_find_tables_with_line_numbers():
    source = (
        "<html>\n<body>\n<table id=\"T\">\n"
        "<tr><td>x</td></tr>\n</table>\n</body></html>"
    )
    root = parse_html(source)
    tables = root.find_all("table")
    assert len(tables) == 1
    table = tables[0]
    assert table.attrs["id"] == "T"
    assert table.line_no == 3
    assert table.end_line == 5
    assert table.source_slice(source).startswith("<table")
    assert table.source_slice(source).endswith("</table>")


def test_nested_table_text_can_be_skipped():
    source = (
        "<table><tr><td>outer<table><tr><td>inner</td></tr></table>"
        "</td></tr></table>"
    )
    table = parse_html(source).find_all("table")[0]
    cell = table.find_all("td")[0]
    assert "outer" in cell.text_content()
    assert "inner" in cell.text_content()
    assert "inner" not in cell.text_content(skip_tags=frozenset({"table"}))


def test_unknown_tags_are_ignored_but_text_is_kept():
    root = parse_html("<div><unknown>保留文本</unknown></div>")
    div = root.first_child_of_type("div")
    assert div is not None
    assert "保留文本" in div.text_content()
    assert root.find_all("unknown") == []


def test_script_and_style_contents_are_suppressed():
    root = parse_html(
        "<div>可见<script>alert('x')</script><style>.a{}</style>尾部</div>"
    )
    text = root.first_child_of_type("div").text_content()
    assert "可见" in text
    assert "尾部" in text
    assert "alert" not in text
    assert ".a" not in text


def test_br_and_block_boundaries_keep_words_separate():
    root = parse_html("<p>alpha<br>beta<span> gamma</span></p>")
    text = " ".join(root.first_child_of_type("p").text_content().split())
    assert text == "alpha beta gamma"


def test_attributes_are_lowercase_and_values_default_to_empty():
    node = parse_html('<table DATA-X="1" hidden></table>').find_all("table")[0]
    assert node.attrs["data-x"] == "1"
    assert node.attrs["hidden"] == ""


def test_first_child_of_type_returns_direct_child_only():
    root = parse_html("<div><table><tr><td>x</td></tr></table></div>")
    div = root.first_child_of_type("div")
    assert div.first_child_of_type("table") is not None
    assert div.first_child_of_type("td") is None
    assert len(div.find_all("td")) == 1


def test_implicit_cell_and_row_closing_is_tolerated():
    table = parse_html("<table><tr><td>a<td>b</table>").find_all("table")[0]
    cells = table.find_all("td")
    assert len(cells) == 2
    assert [cell.text_content().strip() for cell in cells] == ["a", "b"]


def test_parse_file_returns_source_and_document(tmp_path: Path):
    path = tmp_path / "sample.html"
    source = "<p>本地归档</p>"
    path.write_text(source, encoding="utf-8")
    loaded, root = parse_file(path)
    assert loaded == source
    assert root.find_all("p")[0].text_content().strip() == "本地归档"


def test_source_offsets_include_unicode_characters():
    source = "<div>中文</div><table><tr><td>值</td></tr></table>"
    table = parse_html(source).find_all("table")[0]
    assert table.source_slice(source) == "<table><tr><td>值</td></tr></table>"


def test_text_nodes_record_line_numbers():
    root = parse_html("<div>\n<span>第一行</span>\n<span>第二行</span>\n</div>")
    text_nodes = [node for node in root.find_all("#text") if node.text.strip()]
    assert [node.line_no for node in text_nodes] == [2, 3]


def test_empty_document_has_no_tables():
    root = parse_html("")
    assert isinstance(root, Node)
    assert root.find_all("table") == []
