import pytest
from click.types import FloatRange, IntRange

@pytest.mark.parametrize("range_type", [IntRange, FloatRange])
def test_haao_r102_range_rejects_inverted_bounds(range_type):
    with pytest.raises(ValueError, match="minimum cannot be greater than maximum"):
        range_type(10, 5)
