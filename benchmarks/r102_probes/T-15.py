import tablib


def test_haao_r102_latex_export_preserves_zero_and_false():
    data = tablib.Dataset(headers=["zero", "flag", "empty", "missing"])
    data.append([0, False, "", None])

    exported = data.export("latex")

    assert "      0 & False &  &  \\\\" in exported
