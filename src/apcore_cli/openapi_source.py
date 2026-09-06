"""OpenAPI source loading and proxy-hazard detection (FE-15a §4.1 / §4.3).

The CLI is an adapter, not a second implementation: document fetching and
parsing are ``apcore_toolkit.load_spec``'s job and module-ID derivation is
``OpenAPIScanner``'s. What lives here is the CLI-side error surface (a
readable message and exit ``47`` for every way a source can fail) and the one
piece of analysis the toolkit cannot do — proxy-hazard detection, which needs
the *raw* document.

Mirrors ``src/openapi-source.ts`` (TypeScript) and ``src/openapi_source.rs``
(Rust).
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from typing import Any, NoReturn

import click

logger = logging.getLogger("apcore_cli.openapi")

EXIT_INVALID_INPUT = 2
EXIT_SOURCE_ERROR = 47

#: The HTTP methods ``HTTPProxyRegistryWriter`` sends as a JSON **body**. A
#: query parameter declared on one of these is silently misrouted into the
#: body, because ``OpenAPIScanner`` deliberately records no parameter location
#: (a second source of truth that looks authoritative and is ignored). FE-15a
#: cannot fix that — it can and must make it visible.
_BODY_METHODS: frozenset[str] = frozenset({"post", "put", "patch"})

#: Actionable remedy for a missing HTTP stack, per §4.6. Never a bare
#: ImportError: the user needs the extra's name, not a traceback.
_HTTP_EXTRA_HINT = (
    "fetching an http(s):// source needs the 'http-proxy' extra — "
    "install it with: pip install 'apcore-toolkit[http-proxy]'"
)


@dataclass(frozen=True)
class Hazard:
    """One operation FE-15b will be unable to proxy correctly.

    A diagnostic, never a routing decision: hazards are counted separately
    from scanner warnings and never change an exit code in FE-15a.
    """

    module_id: str
    http_method: str
    url_path: str
    parameters: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "http_method": self.http_method,
            "url_path": self.url_path,
            "parameters": list(self.parameters),
        }

    def describe(self) -> str:
        count = len(self.parameters)
        noun = "parameter" if count == 1 else "parameters"
        names = ", ".join(self.parameters)
        return f"{self.http_method} with {count} `in: query` {noun} ({names})"


def parse_headers(headers: list[str] | tuple[str, ...] | None) -> dict[str, str]:
    """Parse repeated ``--header "Key: Value"`` values into a mapping.

    A value with no ``:`` is rejected rather than dropped: a silently ignored
    auth header surfaces as a confusing 401 against the document server.
    """
    parsed: dict[str, str] = {}
    for raw in headers or ():
        if ":" not in raw:
            click.echo(
                f"Error: Invalid --header value {raw!r}: expected 'Key: Value'.",
                err=True,
            )
            sys.exit(EXIT_INVALID_INPUT)
        key, _, value = raw.partition(":")
        key = key.strip()
        if not key:
            click.echo(
                f"Error: Invalid --header value {raw!r}: header name is empty.",
                err=True,
            )
            sys.exit(EXIT_INVALID_INPUT)
        parsed[key] = value.strip()
    return parsed


def _fail(message: str) -> NoReturn:
    click.echo(f"Error: {message}", err=True)
    sys.exit(EXIT_SOURCE_ERROR)


def load_openapi_source(
    source: str,
    headers: list[str] | tuple[str, ...] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Load and parse an OpenAPI document from a path or ``http(s)://`` URL.

    The source is taken **verbatim** — no candidate paths (``/openapi.json``,
    ``/v3/api-docs``, …) are probed, so a wrong URL produces an honest 404
    rather than a surprising success against a different document. Format
    detection is content sniffing, not file extension. All of that is
    ``load_spec``'s behaviour and this function delegates to it.

    ``--openapi-timeout`` is **seconds** in every SDK; the conversion to the
    upstream unit happens at this boundary (Python's ``load_spec`` already
    takes seconds).

    Args:
        source: Local path or ``http(s)://`` URL, verbatim.
        headers: Repeated ``--header "Key: Value"`` values; ignored for local
            files. These exist only for this fetch and are never written into
            a generated artifact (§4.4).
        timeout: Request timeout in seconds.

    Returns:
        The parsed document.

    Raises:
        SystemExit: Exit ``47`` for every unreadable, unfetchable or malformed
            source; exit ``2`` for a malformed ``--header`` value.
    """
    header_map = parse_headers(headers)

    try:
        from apcore_toolkit import load_spec
    except ImportError as exc:  # pragma: no cover - toolkit is a required dep
        _fail(f"Cannot read OpenAPI source '{source}': apcore-toolkit is unavailable ({exc}).")

    try:
        spec = load_spec(source, headers=header_map or None, timeout=timeout)
    except ImportError as exc:
        # httpx lives in the toolkit's `http-proxy` extra and is imported
        # lazily inside load_spec (§4.6).
        _fail(f"Cannot read OpenAPI source '{source}': {_HTTP_EXTRA_HINT} ({exc}).")
    except (json.JSONDecodeError, TypeError) as exc:
        _fail(f"Cannot parse OpenAPI source '{source}': {exc}")
    except FileNotFoundError as exc:
        _fail(f"Cannot read OpenAPI source '{source}': {exc.strerror or exc}")
    except IsADirectoryError as exc:
        _fail(f"Cannot read OpenAPI source '{source}': {exc.strerror or exc}")
    except PermissionError as exc:
        _fail(f"Cannot read OpenAPI source '{source}': {exc.strerror or exc}")
    except OSError as exc:
        _fail(f"Cannot read OpenAPI source '{source}': {exc}")
    except ValueError as exc:
        # `load_spec` wraps malformed YAML in a ValueError; a JSON body that
        # parses to a non-mapping also lands here via `dict(...)`.
        _fail(f"Cannot parse OpenAPI source '{source}': {exc}")
    except Exception as exc:
        # httpx errors are only importable when the extra is installed, so
        # they are classified by module rather than by class.
        if type(exc).__module__.split(".")[0] == "httpx":
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detail = f"HTTP {status}" if status is not None else str(exc)
            _fail(f"Cannot read OpenAPI source '{source}': {detail}")
        raise

    if not isinstance(spec, dict):
        _fail(f"Cannot parse OpenAPI source '{source}': document is not a mapping.")
    return spec


def _parameter_entries(operation: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the operation's ``parameters``, resolving trivial ``$ref``s.

    A malformed entry yields nothing rather than an exception — this is a
    diagnostic, and a document that trips it should still scan.
    """
    raw = operation.get("parameters")
    if not isinstance(raw, list):
        return []

    from apcore_toolkit.openapi import resolve_ref

    entries: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ref = item.get("$ref")
        if isinstance(ref, str):
            try:
                resolved = resolve_ref(ref, spec)
            except Exception:
                # The toolkit's resolve_ref is documented to return {} on a
                # missing/malformed pointer, but this is a diagnostic path —
                # this function's own contract ("a malformed entry yields
                # nothing rather than an exception") must hold even if a
                # future toolkit version (or an external/unresolvable $ref
                # it cannot yet special-case) raises instead of returning {}.
                # An unresolved parameter entry is simply dropped, exactly
                # like a resolution that already came back empty.
                logger.debug("resolve_ref(%r, ...) raised; treating as unresolved.", ref, exc_info=True)
                continue
            if isinstance(resolved, dict) and resolved:
                entries.append(resolved)
            continue
        entries.append(item)
    return entries


def detect_proxy_hazards(spec: dict[str, Any], modules: list[Any]) -> list[Hazard]:
    """Identify operations a future HTTP proxy would misroute (§4.3).

    The toolkit's proxy writer decides body-versus-query by HTTP method
    alone, so a query parameter declared on a ``POST`` / ``PUT`` / ``PATCH``
    operation would be sent in the request body — silently. The CLI holds the
    raw document, which still carries ``parameters[].in``, so it can name the
    affected operations without duplicating any routing logic.

    Args:
        spec: The parsed OpenAPI document.
        modules: The scan result, for module-ID correlation. An operation
            that produced no module (filtered out, deprecated, deduplicated)
            yields no hazard — there is nothing for FE-15b to misroute.

    Returns:
        One :class:`Hazard` per affected operation, in document order. Never
        raises.
    """
    if not isinstance(spec, dict):
        return []

    # Correlate by the routing keys the scanner itself wrote, rather than
    # re-deriving IDs: `derive_module_id` is conformance-pinned across three
    # SDKs and the CLI MUST NOT re-implement or post-process it.
    by_route: dict[tuple[str, str], str] = {}
    for module in modules or ():
        metadata = getattr(module, "metadata", None)
        if not isinstance(metadata, dict):
            continue
        method = metadata.get("http_method")
        path = metadata.get("url_path")
        if isinstance(method, str) and isinstance(path, str):
            by_route.setdefault((method.upper(), path), getattr(module, "module_id", ""))

    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return []

    hazards: list[Hazard] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict) or not isinstance(path, str):
            continue
        for key, operation in path_item.items():
            if not isinstance(key, str) or key.lower() not in _BODY_METHODS:
                continue
            if not isinstance(operation, dict):
                continue
            method = key.upper()
            module_id = by_route.get((method, path))
            if module_id is None:
                continue
            names = tuple(
                param["name"]
                for param in _parameter_entries(operation, spec)
                if param.get("in") == "query" and isinstance(param.get("name"), str)
            )
            if names:
                hazards.append(Hazard(module_id=module_id, http_method=method, url_path=path, parameters=names))
    return hazards


__all__ = ["Hazard", "detect_proxy_hazards", "load_openapi_source", "parse_headers"]
