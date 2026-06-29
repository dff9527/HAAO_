import tablib


def test_haao_r102_html_databook_escapes_sheet_title():
    data = tablib.Dataset(title="<b>A&B</b>", headers=["value"])
    data.append(["safe"])
    book = tablib.Databook([data])

    exported = book.export("html")

    assert exported.startswith("<h3>&lt;b&gt;A&amp;B&lt;/b&gt;</h3>")
    assert "<h3><b>" not in exported
