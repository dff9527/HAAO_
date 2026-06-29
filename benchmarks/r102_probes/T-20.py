from io import StringIO

from tablib.formats._json import JSONFormat


def test_haao_r102_json_detect_rejects_scalar_documents():
    assert JSONFormat.detect(StringIO("42")) is False
    assert JSONFormat.detect(StringIO('[{"name": "Ada"}]')) is True
