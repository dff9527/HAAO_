import tablib

def test_haao_r102_empty_dataset_filter_and_sort():
    data = tablib.Dataset()
    filtered = data.filter("keep")
    sorted_data = data.sort("missing")
    assert isinstance(filtered, tablib.Dataset) and filtered.height == 0
    assert isinstance(sorted_data, tablib.Dataset) and sorted_data.height == 0
