import tablib


def test_haao_r102_xls_export_sanitizes_sheet_title():
    data = tablib.Dataset(
        title="bad name \\/*?:[]qwertyuiopasdfghjklzxcvbnm",
        headers=["value"],
    )
    data.append([1])

    exported = data.export("xls")
    loaded = tablib.Dataset().load(exported, format="xls")

    assert loaded.title == "bad name -------qwertyuiopasdfg"

    book = tablib.Databook([data])
    loaded_book = tablib.Databook().load(book.export("xls"), format="xls")

    assert loaded_book.sheets()[0].title == "bad name -------qwertyuiopasdfg"
