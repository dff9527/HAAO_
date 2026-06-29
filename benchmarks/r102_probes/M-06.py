import json

from marshmallow.exceptions import ValidationError


def test_haao_r102_validation_error_normalizes_bytes_messages():
    error = ValidationError(b"not valid")
    assert error.messages == ["not valid"]
    json.dumps(error.messages)
