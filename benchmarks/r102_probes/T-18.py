import pytest

import tablib


class UnsupportedValue:
    pass


def test_haao_r102_json_unsupported_value_raises_type_error():
    data = tablib.Dataset(headers=["value"])
    data.append([UnsupportedValue()])

    with pytest.raises(TypeError, match="UnsupportedValue.*not JSON serializable"):
        data.export("json")
