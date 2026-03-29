"""Tests for Shell Integration (FE-06)."""

import click
from click.testing import CliRunner

from apcore_cli.shell import (
    _generate_bash_completion,
    _generate_fish_completion,
    _generate_zsh_completion,
    build_program_man_page,
    configure_man_help,
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


class TestGroupedCompletion:
    """Tests for shell completion with grouped (dotted) module IDs."""

    def test_bash_completion_includes_groups(self):
        """Bash completion script contains group extraction logic (split('.')[0])."""
        script = _generate_bash_completion(_PROG)
        # Should contain logic to extract group names from dotted IDs
        assert "split('.')[0]" in script
        # Should contain logic to identify top-level vs grouped modules
        assert "groups" in script

    def test_bash_completion_nested_commands(self):
        """Bash completion script contains logic for COMP_CWORD=2 group command completion."""
        script = _generate_bash_completion(_PROG)
        # Should have a block for position 2 that handles group sub-commands
        assert "COMP_CWORD} -eq 2" in script
        # Should extract sub-commands by splitting on dot
        assert "split('.',1)[1]" in script

    def test_bash_completion_groups_and_builtins_at_position1(self):
        """Position 1 completes builtins + groups + top-level modules."""
        script = _generate_bash_completion(_PROG)
        assert "builtins" in script
        assert "all_ids" in script or "groups" in script

    def test_zsh_completion_includes_groups(self):
        """Zsh completion includes group extraction and group sub-command completion."""
        script = _generate_zsh_completion(_PROG)
        # Should contain group extraction logic
        assert "split('.')[0]" in script
        # Should contain group sub-command completion in the args state
        assert "group_cmds" in script

    def test_zsh_completion_groups_at_position1(self):
        """Zsh completion offers groups alongside built-in commands at position 1."""
        script = _generate_zsh_completion(_PROG)
        assert "groups_and_top" in script

    def test_fish_completion_includes_groups(self):
        """Fish completion includes group extraction and group sub-command completion."""
        script = _generate_fish_completion(_PROG)
        # Should contain group extraction logic
        assert "split('.')[0]" in script
        # Should contain a helper function for group sub-commands
        assert "__apcore_group_cmds" in script


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


class TestBuildProgramManPage:
    def test_generates_roff_with_th_header(self):
        @click.group()
        def cli():
            pass

        @cli.command()
        @click.option("--name", help="Your name")
        def hello(name):
            pass

        roff = build_program_man_page(cli, "test-cli", "1.0.0")
        assert '.TH "TEST-CLI"' in roff
        assert ".SH COMMANDS" in roff
        assert "hello" in roff

    def test_includes_nested_subcommands(self):
        @click.group()
        def cli():
            pass

        @cli.group()
        def grp():
            pass

        @grp.command()
        @click.option("--flag", is_flag=True, help="A flag")
        def sub(flag):
            pass

        roff = build_program_man_page(cli, "mycli", "1.0.0")
        assert "mycli grp sub" in roff

    def test_includes_standard_sections(self):
        @click.group()
        def cli():
            """My test CLI."""

        roff = build_program_man_page(cli, "test-cli", "1.0.0")
        assert ".SH NAME" in roff
        assert ".SH SYNOPSIS" in roff
        assert ".SH DESCRIPTION" in roff
        assert ".SH ENVIRONMENT" in roff
        assert ".SH EXIT CODES" in roff
        assert ".SH SEE ALSO" in roff

    def test_uses_custom_description(self):
        @click.group()
        def cli():
            pass

        roff = build_program_man_page(cli, "test-cli", "1.0.0", description="Custom desc")
        assert "Custom desc" in roff

    def test_includes_command_options(self):
        @click.group()
        def cli():
            pass

        @cli.command()
        @click.option("--output", "-o", help="Output file")
        def build(output):
            pass

        roff = build_program_man_page(cli, "test-cli", "1.0.0")
        assert "Output file" in roff

    def test_skips_help_command(self):
        @click.group()
        def cli():
            pass

        @cli.command()
        def help():
            pass

        roff = build_program_man_page(cli, "test-cli", "1.0.0")
        # "help" should not appear in the COMMANDS section as a listed command
        assert "test\\-cli help" not in roff


class TestConfigureManHelp:
    def test_adds_hidden_man_option(self):
        @click.group()
        def cli():
            pass

        configure_man_help(cli, "test-cli", "1.0.0")
        man_params = [p for p in cli.params if p.name == "man"]
        assert len(man_params) == 1
        assert man_params[0].hidden is True

    def test_man_option_is_flag(self):
        @click.group()
        def cli():
            pass

        configure_man_help(cli, "test-cli", "1.0.0")
        man_params = [p for p in cli.params if p.name == "man"]
        assert man_params[0].is_flag is True
