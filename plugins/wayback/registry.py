from __future__ import annotations

import urllib.parse
from pathlib import Path
from types import ModuleType

from fatlib.browser import BrowserOptions
from fatlib.discovery import discover_external_modules, import_module_from_package


BUILTIN_PARSER_NAMES = ("httpbin",)
BUILTIN_PARSER_BY_HOST = {"httpbin.org": "httpbin"}
PARSER_BY_HOST: dict[str, str] = dict(BUILTIN_PARSER_BY_HOST)
PARSERS: dict[str, ModuleType] = {}
_initialized = False


def _load_builtin_parser(name: str) -> ModuleType:
    module = import_module_from_package("plugins.wayback", f"parsers.{name}")
    if not callable(getattr(module, "extract_links", None)):
        raise SystemExit(f"Parser {name} must export extract_links(snapshot, html, archive).")
    return module


def _validate_render_settings(name: str, module: ModuleType) -> None:
    try:
        BrowserOptions(
            mode=str(getattr(module, "RENDER_MODE", "static")),
            wait_until=str(getattr(module, "RENDER_WAIT_UNTIL", "domcontentloaded")),
            wait_for=getattr(module, "RENDER_WAIT_FOR", None),
            timeout=int(getattr(module, "RENDER_TIMEOUT", None) or 90),
        )
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Wayback parser {name} has invalid browser rendering settings: {exc}") from exc


def configure_external_parsers(plugin_dir: Path) -> None:
    """Load the public parser and parsers copied into the configured root."""

    parsers = {name: _load_builtin_parser(name) for name in BUILTIN_PARSER_NAMES}
    hosts = dict(BUILTIN_PARSER_BY_HOST)
    for name, module in parsers.items():
        _validate_render_settings(name, module)
    for module in discover_external_modules(plugin_dir, "wayback/parsers", "wayback_parsers"):
        if getattr(module, "EXTERNAL_SUPPORT", False):
            continue
        name = Path(str(module.__file__)).stem
        if name in parsers:
            raise SystemExit(f"Duplicate Wayback parser name: {name}")
        if not callable(getattr(module, "extract_links", None)):
            raise SystemExit(
                f"External Wayback parser {module.__file__} must export "
                "extract_links(snapshot, html, archive)."
            )
        _validate_render_settings(name, module)
        declared_hosts = getattr(module, "HOSTS", ())
        if not isinstance(declared_hosts, (tuple, list, set)) or not declared_hosts:
            raise SystemExit(f"External Wayback parser {name} must define non-empty HOSTS")
        for raw_host in declared_hosts:
            host = str(raw_host).strip().lower().rstrip(".")
            if not host or ":" in host:
                raise SystemExit(f"External Wayback parser {name} has invalid HOSTS entry: {raw_host!r}")
            if host in hosts:
                raise SystemExit(f"Duplicate Wayback parser host registration: {host}")
            hosts[host] = name
        parsers[name] = module

    PARSERS.clear()
    PARSERS.update(parsers)
    PARSER_BY_HOST.clear()
    PARSER_BY_HOST.update(hosts)
    global _initialized
    _initialized = True


def _ensure_initialized() -> None:
    if not _initialized:
        configure_external_parsers(Path.home() / ".fat" / "plugins")


def available_parser_names() -> tuple[str, ...]:
    _ensure_initialized()
    return tuple(sorted(PARSERS))


def infer_parser_name(target_url: str, explicit_parser: str | None) -> str:
    _ensure_initialized()
    if explicit_parser:
        return explicit_parser
    host = (urllib.parse.urlsplit(target_url).hostname or "").lower().rstrip(".")
    parser_name = PARSER_BY_HOST.get(host)
    if parser_name:
        return parser_name
    raise SystemExit(f"No parser mapping for host {host!r}; set --parser explicitly.")


def load_parser(parser_name: str) -> ModuleType:
    _ensure_initialized()
    try:
        return PARSERS[parser_name]
    except KeyError as exc:
        available = ", ".join(available_parser_names()) or "-"
        raise SystemExit(
            f"Unknown Wayback parser {parser_name!r}. Available parsers: {available}. "
            "Install the vendor plugin into the external plugin directory if this is a vendor parser"
        ) from exc
