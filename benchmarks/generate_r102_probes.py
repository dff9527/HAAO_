#!/usr/bin/env python3
"""Generate active R-102 probe tests from their reviewed source of truth."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent / "r102_probes"

PROBES: dict[str, str] = {
    "C-01": '''
import pytest
from click.types import FloatRange, IntRange

@pytest.mark.parametrize("range_type", [IntRange, FloatRange])
def test_haao_r102_range_rejects_inverted_bounds(range_type):
    with pytest.raises(ValueError, match="minimum cannot be greater than maximum"):
        range_type(10, 5)
''',
    "C-03": '''
from pathlib import Path
import click
from click.testing import CliRunner

def test_haao_r102_path_expands_and_normalizes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "data"
    target.mkdir()

    @click.command()
    @click.argument("p", type=click.Path(exists=True, path_type=Path))
    def cli(p):
        click.echo(str(p))

    result = CliRunner().invoke(cli, ["~/missing/../data"])
    assert result.exit_code == 0
    assert result.output.strip() == str(target)
''',
    "T-01": '''
import csv
import io
import tablib

def test_haao_r102_csv_formula_injection_escaped():
    values = ["=1+1", "+1", "-1", "@cmd", "safe"]
    data = tablib.Dataset(headers=["v"])
    for value in values:
        data.append([value])

    rows = list(csv.reader(io.StringIO(data.export("csv"))))
    assert [row[0] for row in rows[1:]] == ["'=1+1", "'+1", "'-1", "'@cmd", "safe"]
''',
    "T-02": '''
import tablib

def test_haao_r102_detect_tsv_with_quoted_comma():
    sample = '"a,b"\\tc\\n1\\t2\\n'
    assert tablib.detect_format(sample) == "tsv"
    data = tablib.Dataset().load(sample)
    assert list(data.headers) == ["a,b", "c"]
    assert data[0] == ("1", "2")
''',
    "T-03": '''
import tablib

def test_haao_r102_empty_dataset_filter_and_sort():
    data = tablib.Dataset()
    filtered = data.filter("keep")
    sorted_data = data.sort("missing")
    assert isinstance(filtered, tablib.Dataset) and filtered.height == 0
    assert isinstance(sorted_data, tablib.Dataset) and sorted_data.height == 0
''',
    "T-05": '''
import json
from datetime import datetime
from decimal import Decimal
import tablib

def test_haao_r102_json_serializes_datetime_decimal():
    data = tablib.Dataset(headers=["when", "amt"])
    data.append([datetime(2020, 1, 1, 12, 30), Decimal("1.5")])
    payload = json.loads(data.export("json"))
    assert payload == [{"when": "2020-01-01", "amt": "1.5"}]
''',
    "T-07": '''
import tablib

def test_haao_r102_csv_import_and_append_normalize_headers():
    data = tablib.Dataset().load(" Name , Age \\nAnn,1\\n", format="csv")
    data.append_col(["x"], header=" Note ")
    assert list(data.headers) == ["Name", "Age", "Note"]
''',
}


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for task_id, body in PROBES.items():
        path = ROOT / f"{task_id}.py"
        path.write_text(body.strip() + "\n", encoding="utf-8")
        print(f"wrote {path.name}")


if __name__ == "__main__":
    main()
