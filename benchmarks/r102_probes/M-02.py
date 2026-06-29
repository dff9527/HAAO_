import pytest
from marshmallow import validate
from marshmallow.exceptions import ValidationError


def test_haao_r102_oneof_accepts_iterator_choices():
    validator = validate.OneOf(x for x in (1, 2, 3))
    assert validator(2) == 2
    with pytest.raises(ValidationError):
        validator(4)
