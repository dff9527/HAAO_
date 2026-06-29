import tablib

def test_haao_r102_detect_tsv_with_quoted_comma():
    sample = '"a,b"\tc\n1\t2\n'
    assert tablib.detect_format(sample) == "tsv"
    data = tablib.Dataset().load(sample)
    assert list(data.headers) == ["a,b", "c"]
    assert data[0] == ("1", "2")
