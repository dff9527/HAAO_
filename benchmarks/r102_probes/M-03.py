import pytest
from marshmallow.utils import from_timestamp_ms


def test_haao_r102_from_timestamp_ms_rejects_bool():
    with pytest.raises(ValueError, match="Not a valid POSIX timestamp"):
        from_timestamp_ms(True)
    with pytest.raises(ValueError, match="Not a valid POSIX timestamp"):
        from_timestamp_ms(False)
