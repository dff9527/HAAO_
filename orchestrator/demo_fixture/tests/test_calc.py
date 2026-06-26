from calc import add_one, format_total


def test_add_one_increments_value():
    assert add_one(1) == 2


def test_format_total_labels_sum():
    assert format_total([1, 2, 3]) == "total=6"

