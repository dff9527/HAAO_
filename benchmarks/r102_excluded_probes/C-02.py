"""Archived expectation only; C-02 is NEEDS_REDESIGN and is not benchmarked."""

import click
from click.testing import CliRunner


def test_choice_case_insensitive_returns_canonical_value():
    @click.command()
    @click.option("--mode", type=click.Choice(["Fast", "slow"], case_sensitive=False))
    def cli(mode):
        click.echo(mode)

    result = CliRunner().invoke(cli, ["--mode", "FAST"])
    assert result.exit_code == 0
    assert result.output.strip() == "Fast"
