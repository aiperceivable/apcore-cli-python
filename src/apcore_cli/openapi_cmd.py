"""``apcli openapi`` — read an OpenAPI 3.x document into modules (FE-15a).

Two subcommands under a nested group, mirroring ``apcli config`` and
``apcli init``:

* ``scan``     — read a document and show what it would produce (§4.2)
* ``generate`` — materialize the scan as ``.binding.yaml`` artifacts (§4.4)

Neither registers a module, builds an executor, or issues a request to the
described API. ``scan`` of a local file performs no network I/O at all;
``scan`` of an ``http(s)://`` source fetches exactly one document — the one
named on the command line.

!!! warning "FE-15a does not make an API callable"
    ``generate`` produces binding files. Passing those files to ``--binding``
    does **not** yet produce working commands: ``target`` is a route
    descriptor (``"GET /pets"``), not an import path, so the generic binding
    path cannot register them. That is FE-15b.

Mirrors ``src/openapi-cmd.ts`` (TypeScript) and ``src/openapi_cmd.rs`` (Rust).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import click
from apcore_toolkit import format_csv, format_jsonl, format_modules
from rich.console import Console
from rich.table import Table

from apcore_cli.openapi_source import detect_proxy_hazards, load_openapi_source

logger = logging.getLogger("apcore_cli.openapi")

EXIT_OK = 0
EXIT_WRITE_ERROR = 1
EXIT_INVALID_INPUT = 2
EXIT_SOURCE_ERROR = 47

_SCAN_FORMATS = ["table", "json", "csv", "yaml", "jsonl", "markdown", "skill"]

#: Filename sanitizers, mirroring ``YAMLWriter``'s own so ``--dry-run`` can
#: list real paths and ``--force`` can decide before the writer runs.
_YAML_UNSAFE_RE = re.compile(r"[^a-zA-Z0-9._-]")
_YAML_DOT_RUN_RE = re.compile(r"\.{2,}")


def _scan_options(func: Any) -> Any:
    """The scan-option block shared verbatim by ``scan`` and ``generate``.

    Each maps one-to-one onto a ``OpenAPIScanner.scan`` keyword argument and
    is forwarded unchanged. The scanner's hooks (``transform_operation``,
    ``derive_module_id``, ``transform_module``) are deliberately **not**
    exposed: overriding derivation hands back the cross-SDK naming guarantee,
    which is not something a command-line flag should be able to do silently.
    """
    for option in reversed(
        [
            click.option("--include", default=None, help="Only keep module IDs matching this regex."),
            click.option("--exclude", default=None, help="Drop module IDs matching this regex."),
            click.option("--prefix", default=None, help="Prefix every derived module ID with 'PREFIX.'."),
            click.option(
                "--no-deprecated",
                is_flag=True,
                default=False,
                help="Omit operations marked 'deprecated: true'.",
            ),
            click.option(
                "--header",
                "headers",
                multiple=True,
                help=(
                    "Extra request header for fetching an http(s):// source, as 'Key: Value' "
                    "(repeatable). Never written into a generated artifact."
                ),
            ),
            click.option(
                "--openapi-timeout",
                "timeout",
                type=float,
                default=30.0,
                show_default=True,
                help="Document fetch timeout, in SECONDS.",
            ),
        ]
    ):
        func = option(func)
    return func


def _compile_or_exit(pattern: str | None, flag: str) -> None:
    if pattern is None:
        return
    try:
        re.compile(pattern)
    except re.error as exc:
        click.echo(f"Error: Invalid regex for --{flag}: {exc}", err=True)
        sys.exit(EXIT_INVALID_INPUT)


def _scan_or_exit(
    spec: dict[str, Any],
    *,
    include: str | None,
    exclude: str | None,
    prefix: str | None,
    no_deprecated: bool,
) -> list[Any]:
    """Run ``OpenAPIScanner().scan`` and surface a version fault at exit 47.

    The toolkit's message is reproduced verbatim — it already names the
    offending ``openapi`` value and states that Swagger 2.0 is unsupported.
    """
    from apcore_toolkit import OpenAPIScanner

    try:
        return OpenAPIScanner().scan(
            spec,
            include=include,
            exclude=exclude,
            base_path_prefix=prefix,
            include_deprecated=not no_deprecated,
        )
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(EXIT_SOURCE_ERROR)


def _module_rows(modules: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for module in modules:
        metadata = getattr(module, "metadata", None) or {}
        rows.append(
            {
                "module_id": module.module_id,
                "http_method": metadata.get("http_method", ""),
                "url_path": metadata.get("url_path", ""),
                "target": getattr(module, "target", ""),
                "description": getattr(module, "description", "") or "",
                "tags": list(getattr(module, "tags", []) or []),
                "warnings": list(getattr(module, "warnings", []) or []),
            }
        )
    return rows


def _doc_banner(spec: dict[str, Any], source: str, count: int) -> str:
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    version = spec.get("openapi", "?")
    title = info.get("title") if isinstance(info, dict) else None
    doc_version = info.get("version") if isinstance(info, dict) else None
    descriptor = f"OpenAPI {version}"
    if title:
        descriptor += f", {title}"
        if doc_version:
            descriptor += f" {doc_version}"
    noun = "operation" if count == 1 else "operations"
    return f"{count} {noun} from {source} ({descriptor})"


def _echo_warnings(rows: list[dict[str, Any]]) -> None:
    """Render scanner warnings. They MUST NOT be dropped: the scanner is a
    degrade-with-warning design, and the warning is the only signal that a
    module's flags are incomplete."""
    pairs = [(row["module_id"], warning) for row in rows for warning in row["warnings"]]
    if not pairs:
        return
    noun = "warning" if len(pairs) == 1 else "warnings"
    click.echo(f"\n{len(pairs)} {noun}:")
    for module_id, warning in pairs:
        click.echo(f"  {module_id:<22} {warning}")


def _echo_hazards(hazards: list[Any]) -> None:
    """Render proxy hazards, counted separately from scanner warnings and
    never affecting the exit code in FE-15a."""
    if not hazards:
        return
    noun = "operation" if len(hazards) == 1 else "operations"
    click.echo(f"\n{len(hazards)} {noun} cannot be proxied by FE-15b:")
    for hazard in hazards:
        click.echo(f"  {hazard.module_id:<22} {hazard.describe()}")


def _render_scan(
    modules: list[Any],
    hazards: list[Any],
    spec: dict[str, Any],
    source: str,
    output_format: str,
) -> None:
    rows = _module_rows(modules)

    if output_format in ("markdown", "skill"):
        # `scan()` returns ScannedModule values — exactly the type
        # format_modules already accepts. The `_descriptor_to_scanned` bridge
        # the formatter uses for registry descriptors is simply not needed.
        click.echo(format_modules(modules, style=output_format, display=True))
        return

    if output_format == "json":
        click.echo(
            json.dumps(
                {
                    "source": source,
                    "openapi_version": spec.get("openapi"),
                    "modules": rows,
                    # Hazards sit at the top level rather than inside a
                    # module's `warnings` array: they are a statement about a
                    # *future* execution path, not about the scan that ran.
                    "hazards": [hazard.to_dict() for hazard in hazards],
                },
                indent=2,
                default=str,
            )
        )
        return

    if output_format == "yaml":
        import yaml as _yaml

        click.echo(
            _yaml.dump(
                {
                    "source": source,
                    "openapi_version": spec.get("openapi"),
                    "modules": rows,
                    "hazards": [hazard.to_dict() for hazard in hazards],
                },
                default_flow_style=False,
                allow_unicode=True,
            ).rstrip()
        )
        return

    if output_format == "csv":
        if rows:
            click.echo(format_csv(rows).rstrip())
        return

    if output_format == "jsonl":
        if rows:
            click.echo(format_jsonl(rows).rstrip())
        return

    # table
    click.echo(_doc_banner(spec, source, len(rows)) + "\n")
    if rows:
        table = Table()
        table.add_column("Module ID")
        table.add_column("Route")
        table.add_column("Description")
        table.add_column("Tags")
        for row in rows:
            route = f"{row['http_method']} {row['url_path']}".strip()
            table.add_row(row["module_id"], route, row["description"], ", ".join(row["tags"]))
        Console().print(table)
    _echo_warnings(rows)
    _echo_hazards(hazards)


def _sanitize_for_filename(module_id: str) -> str:
    """The sanitized ``safe_id`` component ``YAMLWriter`` derives from *module_id*."""
    safe = _YAML_UNSAFE_RE.sub("_", module_id)
    return _YAML_DOT_RUN_RE.sub("_", safe)


def _output_filename(module_id: str) -> str:
    """The ``.binding.yaml`` filename ``YAMLWriter`` will produce for *module_id*
    when it is the only module of its sanitized name in the batch.

    Mirrors the writer's own sanitizer so ``--dry-run`` can print real paths
    and ``--force`` can decide before the write happens. Does not account for
    in-batch collisions — see :func:`_planned_output_paths` for that.
    """
    return f"{_sanitize_for_filename(module_id)}.binding.yaml"


def _planned_output_paths(modules: list[Any], out_path: Path) -> list[tuple[Any, Path]]:
    """Pair each scanned module with the ``.binding.yaml`` path it will occupy.

    Mirrors ``YAMLWriter``'s own in-batch collision counter — and TypeScript's
    ``plannedFilenames`` / Rust's ``planned_paths``, which do the same thing
    for the same reason: two module IDs that sanitize to the same base name
    (e.g. ``'GET /a/b'`` and ``'GET /a:b'`` both -> ``a_b``) must NOT collapse
    onto one path here. Before this dedup, a shared bare name made both
    modules' pre-write ``exists()`` check see the identical path, so a single
    pre-existing file at that name caused BOTH to be reported "already
    exists" and skipped — silently dropping the second module's artifact
    entirely rather than writing it to the ``_1``-suffixed name the writer
    would otherwise give it.
    """
    seen: set[str] = set()
    planned: list[tuple[Any, Path]] = []
    for module in modules:
        safe = _sanitize_for_filename(module.module_id)
        filename = f"{safe}.binding.yaml"
        counter = 0
        while filename in seen:
            counter += 1
            filename = f"{safe}_{counter}.binding.yaml"
        seen.add(filename)
        planned.append((module, out_path / filename))
    return planned


def register_openapi_command(apcli_group: click.Group) -> None:
    """Register the ``openapi`` nested group on *apcli_group* (FE-15a §4.7).

    Takes neither a registry nor an executor: FE-15a registers nothing, which
    is exactly why it can ship in all three SDKs simultaneously.
    """

    @apcli_group.group("openapi")
    def openapi_group() -> None:
        """Read an OpenAPI 3.x document into apcore modules.

        Reads and writes files only. Nothing here registers a module, builds
        an executor, or calls the described API — generated .binding.yaml
        artifacts are not yet executable (see `generate --help`).
        """

    # -- scan ---------------------------------------------------------------

    @openapi_group.command("scan")
    @click.argument("source")
    @_scan_options
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(_SCAN_FORMATS),
        default="table",
        help="Output format.",
    )
    def openapi_scan(
        source: str,
        include: str | None,
        exclude: str | None,
        prefix: str | None,
        no_deprecated: bool,
        headers: tuple[str, ...],
        timeout: float,
        output_format: str,
    ) -> None:
        """Show the modules an OpenAPI document would produce.

        Nothing is written and no module is registered. A partially-understood
        document is still a successful scan: warnings and hazards are rendered
        and the exit code stays 0.
        """
        _compile_or_exit(include, "include")
        _compile_or_exit(exclude, "exclude")

        spec = load_openapi_source(source, headers=list(headers), timeout=timeout)
        modules = _scan_or_exit(spec, include=include, exclude=exclude, prefix=prefix, no_deprecated=no_deprecated)
        hazards = detect_proxy_hazards(spec, modules)
        _render_scan(modules, hazards, spec, source, output_format)
        sys.exit(EXIT_OK)

    # -- generate -----------------------------------------------------------

    @openapi_group.command("generate")
    @click.argument("source")
    @click.option(
        "-o",
        "--output",
        "output_dir",
        required=True,
        help="Directory to write generated artifacts into.",
    )
    @click.option("--dry-run", "dry_run", is_flag=True, default=False, help="List paths without writing.")
    @click.option(
        "-f",
        "--force",
        is_flag=True,
        default=False,
        help="Overwrite existing files. Without it an existing file is skipped with a warning.",
    )
    @_scan_options
    def openapi_generate(
        source: str,
        output_dir: str,
        dry_run: bool,
        force: bool,
        include: str | None,
        exclude: str | None,
        prefix: str | None,
        no_deprecated: bool,
        headers: tuple[str, ...],
        timeout: float,
    ) -> None:
        """Write the scanned modules to disk as <id>.binding.yaml artifacts.

        Binding YAML is the only output, in every SDK — the same document
        produces comparable artifacts from Python, TypeScript and Rust. There
        is deliberately no host-language source writer: every toolkit source
        writer resolves `target` as a 'module.path:callable' import path, and
        an OpenAPI operation's target is a route descriptor, so no such writer
        could ever succeed here. Emitting real source for an operation means
        emitting an HTTP proxy implementation, which belongs to FE-15b.

        The generated files are NOT yet executable: passing them to --binding
        does not produce working commands, for the same reason. Making them
        callable is FE-15b.

        No base URL and no credential material is written: headers supplied to
        fetch a protected document exist only for that fetch, and a base URL
        would be metadata nothing in this release consumes.
        """
        _compile_or_exit(include, "include")
        _compile_or_exit(exclude, "exclude")

        spec = load_openapi_source(source, headers=list(headers), timeout=timeout)
        modules = _scan_or_exit(spec, include=include, exclude=exclude, prefix=prefix, no_deprecated=no_deprecated)
        hazards = detect_proxy_hazards(spec, modules)

        out_path = Path(output_dir)
        planned = _planned_output_paths(modules, out_path)

        # Non-destructive default, matching `apcli init`: an existing file is
        # skipped with a WARNING and the command still exits 0. Computed
        # BEFORE the `--dry-run` branch (and shared with the real write path
        # below) so a dry run reports what a real run would actually do,
        # rather than listing every planned path unconditionally regardless
        # of what already exists on disk.
        to_write: list[Any] = []
        skipped_paths: list[Path] = []
        for module, path in planned:
            if path.exists() and not force:
                skipped_paths.append(path)
                continue
            to_write.append(module)

        for path in skipped_paths:
            click.echo(f"WARNING: {path} already exists — skipped. Use -f/--force to overwrite.", err=True)

        if dry_run:
            skipped_set = set(skipped_paths)
            for _module, path in planned:
                if path in skipped_set:
                    continue
                click.echo(f"Would write {path}")
            click.echo(
                f"{len(planned) - len(skipped_paths)} file(s) would be written to {out_path}."
                + (f" {len(skipped_paths)} skipped." if skipped_paths else "")
            )
            _echo_hazards(hazards)
            sys.exit(EXIT_OK)

        results: list[Any] = []
        if to_write:
            from apcore_toolkit import YAMLWriter

            try:
                results = YAMLWriter().write(to_write, str(out_path), verify=True)
            except OSError as exc:
                click.echo(f"Error: Cannot write to '{out_path}': {exc}", err=True)
                sys.exit(EXIT_WRITE_ERROR)
            except Exception as exc:
                click.echo(f"Error: Failed to generate artifacts in '{out_path}': {exc}", err=True)
                sys.exit(EXIT_WRITE_ERROR)

        # `result.path` is set even when the write did not actually succeed
        # (e.g. a pre-write symlink-escape skip) — `verification_error` is the
        # real success/failure signal. It defaults to None both when
        # verification passed and (for a WriteResult from a code path that
        # never runs it) when no verification was requested, so a bare
        # `path` check alone silently counted a security-skip as "Created"
        # and never produced a non-zero exit code for any failure.
        failures = [r for r in results if getattr(r, "verification_error", None) is not None]
        successes = [r for r in results if getattr(r, "verification_error", None) is None]

        for result in successes:
            if getattr(result, "path", None):
                click.echo(f"Created {result.path}")
        for failure in failures:
            click.echo(f"WARNING: {failure.module_id}: {failure.verification_error}", err=True)

        click.echo(
            f"{len(successes)} file(s) written to {out_path}."
            + (f" {len(skipped_paths)} skipped." if skipped_paths else "")
            + (f" {len(failures)} failed." if failures else "")
        )
        _echo_hazards(hazards)
        # A write the user asked for and did not get is a real fault, unlike
        # a skipped file (the documented non-destructive default) — mirrors
        # TypeScript's exit on `results.filter(r => r.verificationError !== null)`.
        sys.exit(EXIT_WRITE_ERROR if failures else EXIT_OK)

    _ = (openapi_scan, openapi_generate)


__all__ = ["register_openapi_command"]
