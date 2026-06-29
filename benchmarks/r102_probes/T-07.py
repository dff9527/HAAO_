import tablib

def test_haao_r102_csv_import_and_append_normalize_headers():
    data = tablib.Dataset().load(" Name , Age \nAnn,1\n", format="csv")
    data.append_col(["x"], header=" Note ")
    assert list(data.headers) == ["Name", "Age", "Note"]
