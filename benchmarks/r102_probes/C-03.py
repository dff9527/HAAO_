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
