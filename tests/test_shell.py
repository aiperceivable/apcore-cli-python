"""Tests for Shell Integration (FE-06)."""

import click
from click.testing import CliRunner

from apcore_cli.shell import (
    _generate_bash_completion,
    _generate_fish_completion,
    _generate_zsh_completion,
    register_shell_commands,
)

_PROG = "apcore-cli"


def _make_cli(prog_name: str = _PROG):
    @click.group(name=prog_name)
    def cli():
        pass

    @cli.command("list")
    @click.option("--tag", multiple=True)
    def list_cmd(tag):
        """List available modules."""
        pass

    @cli.command("exec")
    @click.argument("module_id", required=False)
    def exec_cmd(module_id):
        """Execute an apcore module."""
        pass

    register_shell_commands(cli, prog_name=prog_name)
    return cli


class TestBashCompletion:
    def test_bash_completion_contains_subcommands(self):
        script = _generate_bash_completion(_PROG)
        for cmd in ["exec", "list", "describe", "completion", "man"]:
            assert cmd in script

    def test_bash_completion_has_complete_directive(self):
        script = _generate_bash_completion(_PROG)
        assert f"complete -F _apcore_cli {_PROG}" in script

    def test_bash_completion_valid_syntax(self):
        script = _generate_bash_completion(_PROG)
        assert len(script) > 100

    def test_bash_completion_custom_prog_name(self):
        script = _generate_bash_completion("myproject")
        assert "complete -F _myproject myproject" in script
        assert "_myproject()" in script

    def test_bash_completion_quotes_prog_name_in_command(self):
        # prog_name with spaces must be safely quoted (via shlex.quote) everywhere in the script
        script = _generate_bash_completion("my tool")
        # Appears in both the embedded module-list command and the complete directive
        assert script.count("'my tool'") >= 2


class TestZshFishCompletion:
    def test_zsh_completion_contains_compdef(self):
        script = _generate_zsh_completion(_PROG)
        assert "compdef" in script

    def test_zsh_completion_contains_subcommands(self):
        script = _generate_zsh_completion(_PROG)
        for cmd in ["exec", "list", "describe"]:
            assert cmd in script

    def test_zsh_completion_custom_prog_name(self):
        script = _generate_zsh_completion("myproject")
        assert "compdef _myproject myproject" in script

    def test_zsh_completion_quotes_prog_name_in_directives(self):
        script = _generate_zsh_completion("my tool")
        # compdef directive must quote the prog_name
        assert "compdef _my_tool 'my tool'" in script

    def test_fish_completion_contains_complete(self):
        script = _generate_fish_completion(_PROG)
        assert f"complete -c {_PROG}" in script

    def test_fish_completion_contains_subcommands(self):
        script = _generate_fish_completion(_PROG)
        for cmd in ["exec", "list", "describe"]:
            assert cmd in script

    def test_fish_completion_custom_prog_name(self):
        script = _generate_fish_completion("myproject")
        assert "complete -c myproject" in script

    def test_fish_completion_quotes_prog_name_in_directives(self):
        script = _generate_fish_completion("my tool")
        # All complete -c directives must use the quoted prog_name
        assert "complete -c 'my tool'" in script


class TestCompletionCommand:
    def test_completion_bash(self):
        cli = _make_cli()
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", "bash"])
        assert result.exit_code == 0
        assert "complete" in result.output

    def test_completion_zsh(self):
        cli = _make_cli()
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", "zsh"])
        assert result.exit_code == 0
        assert "compdef" in result.output

    def test_completion_fish(self):
        cli = _make_cli()
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", "fish"])
        assert result.exit_code == 0
        assert f"complete -c {_PROG}" in result.output

    def test_completion_uses_resolved_prog_name(self):
        cli = _make_cli(prog_name="mytool")
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", "bash"])
        assert result.exit_code == 0
        assert "mytool" in result.output

    def test_completion_invalid_shell(self):
        cli = _make_cli()
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", "invalid"])
        assert result.exit_code == 2


class TestManCommand:
    def test_man_list(self):
        cli = _make_cli()
        runner = CliRunner()
        result = runner.invoke(cli, ["man", "list"])
        assert result.exit_code == 0
        assert ".TH" in result.output
        assert f"{_PROG.upper()}-LIST" in result.output

    def test_man_exec(self):
        cli = _make_cli()
        runner = CliRunner()
        result = runner.invoke(cli, ["man", "exec"])
        assert result.exit_code == 0
        assert ".TH" in result.output

    def test_man_unknown_command(self):
        cli = _make_cli()
        runner = CliRunner()
        result = runner.invoke(cli, ["man", "nonexistent"])
        assert result.exit_code == 2

    def test_man_contains_exit_codes(self):
        cli = _make_cli()
        runner = CliRunner()
        result = runner.invoke(cli, ["man", "list"])
        assert "EXIT CODES" in result.output

    def test_man_contains_environment_section(self):
        cli = _make_cli()
        runner = CliRunner()
        result = runner.invoke(cli, ["man", "list"])
        assert "ENVIRONMENT" in result.output
        assert "APCORE_EXTENSIONS_ROOT" in result.output

    def test_man_uses_prog_name(self):
        cli = _make_cli(prog_name="mytool")
        runner = CliRunner()
        result = runner.invoke(cli, ["man", "list"])
        assert result.exit_code == 0
        assert "MYTOOL-LIST" in result.output
        assert "mytool" in result.output

    def test_man_synopsis_reflects_actual_options(self):
        cli = _make_cli()
        runner = CliRunner()
        result = runner.invoke(cli, ["man", "list"])
        assert "SYNOPSIS" in result.output
        # Should reflect the actual --tag option, not generic [ARGUMENTS]
        assert "--tag" in result.output
