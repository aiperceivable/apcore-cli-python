"""Shell Integration — completion scripts and man pages (FE-06)."""

from __future__ import annotations

import re
import shlex
import sys
from datetime import date

import click

from apcore_cli import __version__


def _make_function_name(prog_name: str) -> str:
    """Convert a prog_name like 'my-tool' to a valid shell identifier '_my_tool'."""
    return "_" + re.sub(r"[^a-zA-Z0-9]", "_", prog_name)


def _generate_bash_completion(prog_name: str) -> str:
    fn = _make_function_name(prog_name)
    quoted = shlex.quote(prog_name)
    module_list_cmd = (
        f"{quoted} list --format json 2>/dev/null"
        ' | python3 -c "import sys,json;'
        "[print(m['id']) for m in json.load(sys.stdin)]\" 2>/dev/null"
    )
    # Command to extract group names and top-level (ungrouped) module IDs
    groups_and_top_cmd = (
        f"{quoted} list --format json 2>/dev/null"
        ' | python3 -c "'
        "import sys,json\n"
        "ids=[m['id'] for m in json.load(sys.stdin)]\n"
        "groups=set()\n"
        "top=[]\n"
        "for i in ids:\n"
        "    if '.' in i: groups.add(i.split('.')[0])\n"
        "    else: top.append(i)\n"
        "print(' '.join(sorted(groups)+sorted(top)))\n"
        '" 2>/dev/null'
    )
    # Command to list sub-commands for a given group (uses shell variable $grp)
    group_cmds_cmd = (
        f"{quoted} list --format json 2>/dev/null"
        ' | python3 -c "'
        "import sys,json,os\n"
        "g=os.environ['_APCORE_GRP']\n"
        "ids=[m['id'] for m in json.load(sys.stdin)]\n"
        "for i in ids:\n"
        "    if '.' in i and i.split('.')[0]==g: print(i.split('.',1)[1])\n"
        '" 2>/dev/null'
    )
    return (
        f"{fn}() {{\n"
        "    local cur prev\n"
        "    COMPREPLY=()\n"
        '    cur="${COMP_WORDS[COMP_CWORD]}"\n'
        '    prev="${COMP_WORDS[COMP_CWORD-1]}"\n'
        "\n"
        "    if [[ ${COMP_CWORD} -eq 1 ]]; then\n"
        f"        local all_ids=$({groups_and_top_cmd})\n"
        '        local builtins="completion describe exec init list man"\n'
        '        COMPREPLY=( $(compgen -W "${builtins} ${all_ids}" -- ${cur}) )\n'
        "        return 0\n"
        "    fi\n"
        "\n"
        f'    if [[ "${{COMP_WORDS[1]}}" == "exec" && ${{COMP_CWORD}} -eq 2 ]]; then\n'
        f"        local modules=$({module_list_cmd})\n"
        '        COMPREPLY=( $(compgen -W "${modules}" -- ${cur}) )\n'
        "        return 0\n"
        "    fi\n"
        "\n"
        "    if [[ ${COMP_CWORD} -eq 2 ]]; then\n"
        '        local grp="${COMP_WORDS[1]}"\n'
        f'        local cmds=$(export _APCORE_GRP="$grp"; {group_cmds_cmd})\n'
        '        COMPREPLY=( $(compgen -W "${cmds}" -- ${cur}) )\n'
        "        return 0\n"
        "    fi\n"
        "}\n"
        f"complete -F {fn} {quoted}\n"
    )


def _generate_zsh_completion(prog_name: str) -> str:
    fn = _make_function_name(prog_name)
    quoted = shlex.quote(prog_name)
    module_list_cmd = (
        f"{quoted} list --format json 2>/dev/null"
        ' | python3 -c "import sys,json;'
        "[print(m['id']) for m in json.load(sys.stdin)]\" 2>/dev/null"
    )
    # Command to extract group names and top-level module IDs for position 1
    groups_and_top_cmd = (
        f"{quoted} list --format json 2>/dev/null"
        ' | python3 -c "'
        "import sys,json\n"
        "ids=[m['id'] for m in json.load(sys.stdin)]\n"
        "groups=set()\n"
        "top=[]\n"
        "for i in ids:\n"
        "    if '.' in i: groups.add(i.split('.')[0])\n"
        "    else: top.append(i)\n"
        "print(' '.join(sorted(groups)+sorted(top)))\n"
        '" 2>/dev/null'
    )
    # Command to list sub-commands for a given group ($1 is the group name)
    group_cmds_cmd = (
        f"{quoted} list --format json 2>/dev/null"
        ' | python3 -c "'
        "import sys,json,os\n"
        "g=os.environ['_APCORE_GRP']\n"
        "ids=[m['id'] for m in json.load(sys.stdin)]\n"
        "for i in ids:\n"
        "    if '.' in i and i.split('.')[0]==g: print(i.split('.',1)[1])\n"
        '" 2>/dev/null'
    )
    return (
        f"#compdef {prog_name}\n"
        "\n"
        f"{fn}() {{\n"
        "    local -a commands groups_and_top\n"
        "    commands=(\n"
        "        'exec:Execute an apcore module'\n"
        "        'list:List available modules'\n"
        "        'describe:Show module metadata and schema'\n"
        "        'completion:Generate shell completion script'\n"
        "        'init:Scaffolding commands'\n"
        "        'man:Generate man page'\n"
        "    )\n"
        "\n"
        "    _arguments -C \\\n"
        "        '1:command:->command' \\\n"
        "        '*::arg:->args'\n"
        "\n"
        '    case "$state" in\n'
        "        command)\n"
        f"            groups_and_top=($({groups_and_top_cmd}))\n"
        f"            _describe -t commands '{prog_name} commands' commands\n"
        "            compadd -a groups_and_top\n"
        "            ;;\n"
        "        args)\n"
        '            case "${words[1]}" in\n'
        "                exec)\n"
        "                    local modules\n"
        f"                    modules=($({module_list_cmd}))\n"
        "                    compadd -a modules\n"
        "                    ;;\n"
        "                *)\n"
        "                    local -a group_cmds\n"
        f'                    group_cmds=($(export _APCORE_GRP="${{words[1]}}"; {group_cmds_cmd}))\n'
        "                    compadd -a group_cmds\n"
        "                    ;;\n"
        "            esac\n"
        "            ;;\n"
        "    esac\n"
        "}\n"
        "\n"
        f"compdef {fn} {quoted}\n"
    )


def _generate_fish_completion(prog_name: str) -> str:
    quoted = shlex.quote(prog_name)
    module_list_cmd = (
        f"{quoted} list --format json 2>/dev/null"
        ' | python3 -c \\"import sys,json;'
        "[print(m['id']) for m in json.load(sys.stdin)]\\\" 2>/dev/null"
    )
    # Fish command to extract group names and top-level module IDs
    groups_and_top_cmd = (
        f"{quoted} list --format json 2>/dev/null"
        ' | python3 -c \\"'
        "import sys,json\\n"
        "ids=[m['id'] for m in json.load(sys.stdin)]\\n"
        "groups=set()\\n"
        "top=[]\\n"
        "for i in ids:\\n"
        "    if '.' in i: groups.add(i.split('.')[0])\\n"
        "    else: top.append(i)\\n"
        "print('\\\\n'.join(sorted(groups)+sorted(top)))\\n"
        '\\" 2>/dev/null'
    )
    # Fish command to list sub-commands for a given group
    # Uses $argv[1] as the group name passed by the function
    group_cmds_fish_fn = (
        f"function __apcore_group_cmds\n"
        f"    set -l grp $argv[1]\n"
        f"    {quoted} list --format json 2>/dev/null"
        ' | python3 -c \\"'
        "import sys,json,os\\n"
        "g=os.environ['_APCORE_GRP']\\n"
        "ids=[m['id'] for m in json.load(sys.stdin)]\\n"
        "for i in ids:\\n"
        "    if '.' in i and i.split('.')[0]==g: print(i.split('.',1)[1])\\n"
        '\\" 2>/dev/null\n'
        "end\n"
    )
    return (
        f"# Fish completions for {prog_name}\n"
        f"\n"
        f"{group_cmds_fish_fn}"
        f"\n"
        f'complete -c {quoted} -n "__fish_use_subcommand"'
        ' -a exec -d "Execute an apcore module"\n'
        f'complete -c {quoted} -n "__fish_use_subcommand"'
        ' -a list -d "List available modules"\n'
        f'complete -c {quoted} -n "__fish_use_subcommand"'
        ' -a describe -d "Show module metadata and schema"\n'
        f'complete -c {quoted} -n "__fish_use_subcommand"'
        ' -a completion -d "Generate shell completion script"\n'
        f'complete -c {quoted} -n "__fish_use_subcommand"'
        ' -a init -d "Scaffolding commands"\n'
        f'complete -c {quoted} -n "__fish_use_subcommand"'
        ' -a man -d "Generate man page"\n'
        f'complete -c {quoted} -n "__fish_use_subcommand"'
        f' -a "({groups_and_top_cmd})" -d "Module group or command"\n'
        "\n"
        f'complete -c {quoted} -n "__fish_seen_subcommand_from exec"'
        f' -a "({module_list_cmd})"\n'
    )


def _build_synopsis(command: click.Command | None, prog_name: str, command_name: str) -> str:
    """Build a synopsis line reflecting actual options and arguments."""
    if command is None:
        return f"\\fB{prog_name} {command_name}\\fR [OPTIONS]"

    parts = [f"\\fB{prog_name} {command_name}\\fR"]
    for param in command.params:
        if isinstance(param, click.Option):
            flag = param.opts[0]
            if param.is_flag:
                parts.append(f"[{flag}]")
            elif param.required:
                type_upper = (param.type.name if hasattr(param.type, "name") else "VALUE").upper()
                parts.append(f"{flag} \\fI{type_upper}\\fR")
            else:
                type_upper = (param.type.name if hasattr(param.type, "name") else "VALUE").upper()
                parts.append(f"[{flag} \\fI{type_upper}\\fR]")
        elif isinstance(param, click.Argument):
            meta = param.human_readable_name.upper()
            if param.required:
                parts.append(f"\\fI{meta}\\fR")
            else:
                parts.append(f"[\\fI{meta}\\fR]")
    return " ".join(parts)


def _generate_man_page(command_name: str, command: click.Command | None, prog_name: str) -> str:
    """Generate a roff-formatted man page for a command."""
    today = date.today().strftime("%Y-%m-%d")
    title = f"{prog_name}-{command_name}".upper()
    pkg_label = f"{prog_name} {__version__}"
    manual_label = f"{prog_name} Manual"

    sections: list[str] = []
    sections.append(f'.TH "{title}" "1" "{today}" "{pkg_label}" "{manual_label}"')

    sections.append(".SH NAME")
    desc = (command.help or command_name) if command else command_name
    # Collapse multi-line help to a single short phrase for NAME
    name_desc = desc.split("\n")[0].rstrip(".")
    sections.append(f"{prog_name}-{command_name} \\- {name_desc}")

    sections.append(".SH SYNOPSIS")
    sections.append(_build_synopsis(command, prog_name, command_name))

    if command and command.help:
        sections.append(".SH DESCRIPTION")
        # Escape roff special chars in description
        sections.append(command.help.replace("\\", "\\\\").replace("-", "\\-"))

    if command and any(isinstance(p, click.Option) for p in command.params):
        sections.append(".SH OPTIONS")
        for param in command.params:
            if isinstance(param, click.Option):
                flag = ", ".join(param.opts)
                type_name = param.type.name.upper() if hasattr(param.type, "name") else "VALUE"
                sections.append(".TP")
                if param.is_flag:
                    sections.append(f"\\fB{flag}\\fR")
                else:
                    sections.append(f"\\fB{flag}\\fR \\fI{type_name}\\fR")
                if param.help:
                    sections.append(param.help)
                if param.default is not None and not param.is_flag:
                    sections.append(f"Default: {param.default}.")

    sections.append(".SH ENVIRONMENT")
    sections.append(".TP")
    sections.append("\\fBAPCORE_EXTENSIONS_ROOT\\fR")
    sections.append("Path to the apcore extensions directory. Overrides the default \\fI./extensions\\fR.")
    sections.append(".TP")
    sections.append("\\fBAPCORE_CLI_AUTO_APPROVE\\fR")
    sections.append(
        "Set to \\fB1\\fR to bypass approval prompts for modules that require human-in-the-loop confirmation."
    )
    sections.append(".TP")
    sections.append("\\fBAPCORE_CLI_LOGGING_LEVEL\\fR")
    sections.append(
        "CLI-specific logging verbosity. One of: DEBUG, INFO, WARNING, ERROR. "
        "Takes priority over \\fBAPCORE_LOGGING_LEVEL\\fR. Default: WARNING."
    )
    sections.append(".TP")
    sections.append("\\fBAPCORE_LOGGING_LEVEL\\fR")
    sections.append(
        "Global apcore logging verbosity. One of: DEBUG, INFO, WARNING, ERROR. "
        "Used as fallback when \\fBAPCORE_CLI_LOGGING_LEVEL\\fR is not set. Default: WARNING."
    )
    sections.append(".TP")
    sections.append("\\fBAPCORE_AUTH_API_KEY\\fR")
    sections.append("API key for authenticating with the apcore registry.")

    sections.append(".SH EXIT CODES")
    exit_codes = [
        ("0", "Success."),
        ("1", "Module execution error."),
        ("2", "Invalid CLI input or missing argument."),
        ("44", "Module not found, disabled, or failed to load."),
        ("45", "Input failed JSON Schema validation."),
        ("46", "Approval denied, timed out, or no interactive terminal available."),
        ("47", "Configuration error (extensions directory not found or unreadable)."),
        ("48", "Schema contains a circular \\fB$ref\\fR."),
        ("77", "ACL denied — insufficient permissions for this module."),
        ("130", "Execution cancelled by user (SIGINT / Ctrl\\-C)."),
    ]
    for code, meaning in exit_codes:
        sections.append(f".TP\n\\fB{code}\\fR\n{meaning}")

    sections.append(".SH SEE ALSO")
    see_also = [
        f"\\fB{prog_name}\\fR(1)",
        f"\\fB{prog_name}\\-list\\fR(1)",
        f"\\fB{prog_name}\\-describe\\fR(1)",
        f"\\fB{prog_name}\\-completion\\fR(1)",
    ]
    sections.append(", ".join(see_also))

    return "\n".join(sections)


def _roff_escape(s: str) -> str:
    """Escape a string for roff output."""
    return s.replace("\\", "\\\\").replace("-", "\\-").replace("'", "\\(aq")


def build_program_man_page(
    cli: click.Group,
    prog_name: str,
    version: str,
    description: str | None = None,
    docs_url: str | None = None,
) -> str:
    """Build a complete roff man page for the entire CLI program.

    Covers all registered commands including downstream business commands
    injected via GroupedModuleGroup.
    """
    today = date.today().isoformat()
    desc = description or cli.help or f"{prog_name} CLI"
    s: list[str] = []

    s.append(f'.TH "{prog_name.upper()}" "1" "{today}" "{prog_name} {version}" "{prog_name} Manual"')

    s.append(".SH NAME")
    s.append(f"{prog_name} \\- {_roff_escape(desc)}")

    s.append(".SH SYNOPSIS")
    s.append(f"\\fB{prog_name}\\fR [\\fIglobal\\-options\\fR] \\fIcommand\\fR [\\fIcommand\\-options\\fR]")

    s.append(".SH DESCRIPTION")
    s.append(_roff_escape(desc))

    # Global options
    ctx = click.Context(cli, info_name=prog_name)
    params = cli.get_params(ctx)
    visible_params = [p for p in params if not getattr(p, "hidden", False) and p.name not in ("help", "version", "man")]
    if visible_params:
        s.append(".SH GLOBAL OPTIONS")
        for p in visible_params:
            record = p.get_help_record(ctx)
            if record:
                s.append(".TP")
                s.append(f"\\fB{_roff_escape(record[0])}\\fR")
                s.append(_roff_escape(record[1]))

    # Commands
    cmd_names = cli.list_commands(ctx)
    if cmd_names:
        s.append(".SH COMMANDS")
        for name in sorted(cmd_names):
            if name == "help":
                continue
            cmd = cli.get_command(ctx, name)
            if cmd is None:
                continue

            cmd_desc = cmd.get_short_help_str() if cmd else ""
            s.append(".TP")
            s.append(f"\\fB{prog_name} {_roff_escape(name)}\\fR")
            if cmd_desc:
                s.append(_roff_escape(cmd_desc))

            # Command options
            sub_ctx = click.Context(cmd, info_name=name, parent=ctx)
            sub_params = [
                p for p in cmd.get_params(sub_ctx) if not getattr(p, "hidden", False) and p.name not in ("help",)
            ]
            for p in sub_params:
                record = p.get_help_record(sub_ctx)
                if record:
                    s.append(".RS")
                    s.append(".TP")
                    s.append(f"\\fB{_roff_escape(record[0])}\\fR")
                    s.append(_roff_escape(record[1]))
                    s.append(".RE")

            # Nested subcommands (groups)
            if isinstance(cmd, click.Group):
                sub_names = cmd.list_commands(sub_ctx)
                for sub_name in sorted(sub_names):
                    if sub_name == "help":
                        continue
                    sub_cmd = cmd.get_command(sub_ctx, sub_name)
                    if sub_cmd is None:
                        continue
                    sub_desc = sub_cmd.get_short_help_str() if sub_cmd else ""
                    s.append(".TP")
                    s.append(f"\\fB{prog_name} {_roff_escape(name)} {_roff_escape(sub_name)}\\fR")
                    if sub_desc:
                        s.append(_roff_escape(sub_desc))
                    nested_ctx = click.Context(sub_cmd, info_name=sub_name, parent=sub_ctx)
                    nested_params = [
                        p
                        for p in sub_cmd.get_params(nested_ctx)
                        if not getattr(p, "hidden", False) and p.name not in ("help",)
                    ]
                    for p in nested_params:
                        record = p.get_help_record(nested_ctx)
                        if record:
                            s.append(".RS")
                            s.append(".TP")
                            s.append(f"\\fB{_roff_escape(record[0])}\\fR")
                            s.append(_roff_escape(record[1]))
                            s.append(".RE")

    # Environment
    s.append(".SH ENVIRONMENT")
    s.append(".TP")
    s.append("\\fBAPCORE_EXTENSIONS_ROOT\\fR")
    s.append("Path to the apcore extensions directory.")
    s.append(".TP")
    s.append("\\fBAPCORE_CLI_AUTO_APPROVE\\fR")
    s.append("Set to \\fB1\\fR to bypass approval prompts.")
    s.append(".TP")
    s.append("\\fBAPCORE_CLI_LOGGING_LEVEL\\fR")
    s.append("CLI\\-specific logging verbosity (DEBUG|INFO|WARNING|ERROR).")

    # Exit codes
    s.append(".SH EXIT CODES")
    codes = [
        ("0", "Success."),
        ("1", "Module execution error."),
        ("2", "Invalid input."),
        ("44", "Module not found."),
        ("45", "Schema validation error."),
        ("46", "Approval denied or timed out."),
        ("47", "Configuration error."),
        ("77", "ACL denied."),
        ("130", "Cancelled by user (SIGINT)."),
    ]
    for code, meaning in codes:
        s.append(f".TP\n\\fB{code}\\fR\n{meaning}")

    s.append(".SH SEE ALSO")
    s.append(f"\\fB{prog_name} \\-\\-help \\-\\-verbose\\fR for full option list.")
    if docs_url:
        s.append(f".PP\nFull documentation at \\fI{_roff_escape(docs_url)}\\fR")

    return "\n".join(s)


def configure_man_help(
    cli: click.Group,
    prog_name: str,
    version: str,
    description: str | None = None,
    docs_url: str | None = None,
) -> None:
    """Configure --help --man support on a Click CLI group.

    When --man is passed with --help, outputs a complete roff man page
    covering all registered commands. Downstream projects call this once
    to get man page generation for free.

    .. note::
        Call this **after** all commands are registered on ``cli``.
        The argv pre-parse triggers immediate man page generation, so
        commands added later will not appear in the output.

    Usage:
        configure_man_help(cli, "reach", "0.2.0", "ReachForge", "https://reachforge.dev/docs")
    """
    # Add --man as a hidden Click option
    cli.params.append(
        click.Option(
            ["--man"],
            is_flag=True,
            default=False,
            hidden=True,
            help="Output man page in roff format (use with --help).",
        )
    )

    # Pre-parse: if both --help and --man in argv, generate man page and exit
    args = sys.argv[1:]
    if "--man" in args and ("--help" in args or "-h" in args):
        roff = build_program_man_page(cli, prog_name, version, description, docs_url)
        click.echo(roff)
        sys.exit(0)


def register_shell_commands(cli: click.Group, prog_name: str = "apcore-cli") -> None:
    """Register completion and man commands."""

    @cli.command("completion")
    @click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
    @click.pass_context
    def completion_cmd(ctx: click.Context, shell: str) -> None:
        """Generate a shell completion script and print it to stdout.

        \b
        Install (add to your shell profile):
          bash:  eval "$(PROG completion bash)"   or source <(PROG completion bash)
          zsh:   eval "$(PROG completion zsh)"    or source <(PROG completion zsh)
          fish:  PROG completion fish | source
        """
        resolved = ctx.find_root().info_name or prog_name
        generators = {
            "bash": lambda: _generate_bash_completion(resolved),
            "zsh": lambda: _generate_zsh_completion(resolved),
            "fish": lambda: _generate_fish_completion(resolved),
        }
        click.echo(generators[shell]())

    @cli.command("man")
    @click.argument("command")
    @click.pass_context
    def man_cmd(ctx: click.Context, command: str) -> None:
        """Generate a roff man page for COMMAND and print it to stdout.

        \b
        View immediately:
          PROG man list | man -
          PROG man describe | col -bx | less

        Install system-wide:
          PROG man list > /usr/local/share/man/man1/PROG-list.1
          mandb   # (Linux)   or   /usr/libexec/makewhatis   # (macOS)
        """
        parent = ctx.parent
        if parent is None:
            click.echo(f"Error: Unknown command '{command}'.", err=True)
            sys.exit(2)

        resolved_prog = ctx.find_root().info_name or prog_name
        parent_group = parent.command
        cmd = parent_group.commands.get(command) if isinstance(parent_group, click.Group) else None

        known_builtins = {"completion", "describe", "exec", "init", "list", "man"}
        if cmd is None and command not in known_builtins:
            click.echo(f"Error: Unknown command '{command}'.", err=True)
            sys.exit(2)

        roff = _generate_man_page(command, cmd, resolved_prog)
        click.echo(roff)
