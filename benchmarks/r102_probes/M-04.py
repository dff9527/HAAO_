from marshmallow import fields


def test_haao_r102_float_as_string_shortest_decimal():
    field = fields.Float(as_string=True)
    assert field.serialize("value", {"value": 0.1 + 0.2}) == "0.3"
