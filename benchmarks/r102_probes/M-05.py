from marshmallow import Schema, fields


class _PartialDataKeySchema(Schema):
    internal = fields.Integer(data_key="external", required=True)


def test_haao_r102_partial_honors_data_key():
    assert _PartialDataKeySchema().load({}, partial=("external",)) == {}
