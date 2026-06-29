import pytest
from marshmallow import validate


def test_haao_r102_length_rejects_inverted_bounds():
    with pytest.raises(ValueError, match="minimum cannot be greater than maximum"):
        validate.Length(min=10, max=5)
