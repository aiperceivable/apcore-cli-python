"""Tests for init command (Phase 1)."""

import click
import pytest
from click.testing import CliRunner

from apcore_cli.init_cmd import register_init_command


@pytest.fixture
def cli():
    @click.group()
    def root():
        pass

    register_init_command(root)
    return root


class TestInitModule:
    def test_init_convention_creates_file(self, cli, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "module", "ops.deploy", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "Created" in result.output
        # Check file exists
        files = list(tmp_path.rglob("*.py"))
        assert len(files) >= 1

    def test_init_decorator_creates_file(self, cli, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "module", "ops.deploy", "--style", "decorator", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        files = list(tmp_path.rglob("*.py"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "@module" in content
        assert 'id="ops.deploy"' in content

    def test_init_binding_creates_yaml(self, cli, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "module", "ops.deploy", "--style", "binding", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        yaml_files = list(tmp_path.rglob("*.yaml"))
        assert len(yaml_files) == 1
        content = yaml_files[0].read_text()
        assert "ops.deploy" in content
        from apcore_toolkit import BindingLoader

        for strict in (False, True):
            modules = BindingLoader().load(str(yaml_files[0]), strict=strict)
            assert len(modules) == 1
            assert modules[0].module_id == "ops.deploy"
            assert modules[0].input_schema == {"type": "object", "properties": {}}
            assert modules[0].output_schema["properties"]["status"] == {"type": "string"}

    def test_init_convention_has_cli_group(self, cli, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "module", "ops.deploy", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        files = list(tmp_path.rglob("*.py"))
        content = files[0].read_text()
        assert "CLI_GROUP" in content

    def test_init_with_description(self, cli, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "init",
                "module",
                "ops.deploy",
                "--dir",
                str(tmp_path),
                "-d",
                "Deploy to production",
            ],
        )
        assert result.exit_code == 0
        files = list(tmp_path.rglob("*.py"))
        content = files[0].read_text()
        assert "Deploy to production" in content

    def test_init_module_help(self, cli):
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "module", "--help"])
        assert result.exit_code == 0
        assert "decorator" in result.output
        assert "convention" in result.output
        assert "binding" in result.output


class TestInitModuleForce:
    """W4: init module must refuse to clobber existing files unless --force is set."""

    def test_rerun_convention_without_force_refuses(self, cli, tmp_path):
        runner = CliRunner()
        # First run — creates the file
        r1 = runner.invoke(cli, ["init", "module", "ops.deploy", "--dir", str(tmp_path)])
        assert r1.exit_code == 0
        files = list(tmp_path.rglob("*.py"))
        assert len(files) == 1
        target = files[0]
        target.write_text("# user edits\ndef deploy(): return {'custom': True}\n")
        original = target.read_text()

        # Second run — should refuse to overwrite
        r2 = runner.invoke(cli, ["init", "module", "ops.deploy", "--dir", str(tmp_path)])
        assert r2.exit_code == 0, r2.output  # command itself doesn't abort
        assert "already exists" in r2.output or "already exists" in (r2.stderr_bytes or b"").decode()
        assert target.read_text() == original, "User edits must be preserved"

    def test_rerun_decorator_without_force_refuses(self, cli, tmp_path):
        runner = CliRunner()
        r1 = runner.invoke(cli, ["init", "module", "ops.deploy", "--style", "decorator", "--dir", str(tmp_path)])
        assert r1.exit_code == 0
        files = list(tmp_path.rglob("*.py"))
        target = files[0]
        target.write_text("# user impl")
        runner.invoke(cli, ["init", "module", "ops.deploy", "--style", "decorator", "--dir", str(tmp_path)])
        assert target.read_text() == "# user impl"

    def test_rerun_with_force_overwrites(self, cli, tmp_path):
        runner = CliRunner()
        r1 = runner.invoke(cli, ["init", "module", "ops.deploy", "--dir", str(tmp_path)])
        assert r1.exit_code == 0
        files = list(tmp_path.rglob("*.py"))
        target = files[0]
        target.write_text("# user edits")
        r2 = runner.invoke(cli, ["init", "module", "ops.deploy", "--dir", str(tmp_path), "--force"])
        assert r2.exit_code == 0
        assert target.read_text() != "# user edits"
        assert "TODO" in target.read_text()

    def test_rerun_binding_yaml_without_force_refuses(self, cli, tmp_path):
        runner = CliRunner()
        r1 = runner.invoke(cli, ["init", "module", "ops.deploy", "--style", "binding", "--dir", str(tmp_path)])
        assert r1.exit_code == 0
        yaml_files = list(tmp_path.rglob("*.yaml"))
        target = yaml_files[0]
        target.write_text("# hand-edited")
        runner.invoke(cli, ["init", "module", "ops.deploy", "--style", "binding", "--dir", str(tmp_path)])
        assert target.read_text() == "# hand-edited"

    def test_binding_src_created_inside_dir(self, cli, tmp_path):
        """--style binding --dir X must create both yaml and src inside X, not in cwd."""
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "module", "ops.deploy", "--style", "binding", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        # ALL files must land under tmp_path — nothing in cwd
        py_files = list(tmp_path.rglob("*.py"))
        assert len(py_files) == 1, f"Expected 1 .py under --dir, got {py_files}"

    def test_rerun_binding_src_without_force_emits_message(self, cli, tmp_path):
        """Re-running binding init without --force must echo a refusal for the src file."""
        runner = CliRunner()
        r1 = runner.invoke(cli, ["init", "module", "ops.deploy", "--style", "binding", "--dir", str(tmp_path)])
        assert r1.exit_code == 0
        py_files = list(tmp_path.rglob("*.py"))
        assert len(py_files) == 1
        py_files[0].write_text("# user edits")

        r2 = runner.invoke(cli, ["init", "module", "ops.deploy", "--style", "binding", "--dir", str(tmp_path)])
        assert r2.exit_code == 0
        combined = (r2.output or "") + ((r2.stderr_bytes or b"").decode())
        assert "already exists" in combined, "Silent refusal must now emit an 'already exists' message"
        assert py_files[0].read_text() == "# user edits", "User edits must be preserved"
