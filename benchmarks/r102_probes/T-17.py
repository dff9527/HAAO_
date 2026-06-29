import tablib


def test_haao_r102_html_th_row_imports_as_headers_without_thead():
    raw = (
        "<table>"
        "<tr><th>Name</th><th>Age</th></tr>"
        "<tr><td>Ann</td><td>1</td></tr>"
        "</table>"
    )

    data = tablib.Dataset().load(raw, format="html")

    assert list(data.headers) == ["Name", "Age"]
    assert data[0] == ("Ann", "1")
