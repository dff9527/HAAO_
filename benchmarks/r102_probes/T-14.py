import tablib


def test_haao_r102_jira_export_preserves_zero_and_false():
    data = tablib.Dataset(headers=["zero", "flag", "empty", "missing"])
    data.append([0, False, "", None])

    assert data.export("jira") == (
        "||zero||flag||empty||missing||\n"
        "|0|False| | |"
    )
