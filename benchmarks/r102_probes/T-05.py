import json
from datetime import datetime
from decimal import Decimal
import tablib

def test_haao_r102_json_serializes_datetime_decimal():
    data = tablib.Dataset(headers=["when", "amt"])
    data.append([datetime(2020, 1, 1, 12, 30), Decimal("1.5")])
    payload = json.loads(data.export("json"))
    assert payload == [{"when": "2020-01-01T12:30:00", "amt": "1.5"}]
