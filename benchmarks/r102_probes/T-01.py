import csv
import io
import tablib

def test_haao_r102_csv_formula_injection_escaped():
    values = ["=1+1", "+1", "-1", "@cmd", "safe"]
    data = tablib.Dataset(headers=["v"])
    for value in values:
        data.append([value])

    default_rows = list(csv.reader(io.StringIO(data.export("csv"))))
    assert [row[0] for row in default_rows[1:]] == values

    escaped_rows = list(csv.reader(io.StringIO(data.export("csv", escape=True))))
    assert [row[0] for row in escaped_rows[1:]] == [
        "'=1+1",
        "'+1",
        "'-1",
        "'@cmd",
        "safe",
    ]
