#!/usr/bin/env python3
"""Verify that the declared plugin API is present in generated documentation."""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REFERENCE_ROOT = REPOSITORY_ROOT / "docs" / "plugin-api" / "reference"

# Keep this list aligned with the modules passed to pdoc by
# build_plugin_api_docs.sh. Each module's __all__ declaration defines the
# names that form its documented public API.
DOCUMENTED_MODULES = {
    "plugins.base": "plugins/base.html",
    "plugins.scraper.base": "plugins/scraper/base.html",
    "plugins.wayback.parsers.api": "plugins/wayback/parsers/api.html",
}


def _load_module(module_name: str) -> ModuleType:
    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))
    return importlib.import_module(module_name)


def _has_anchor(html: str, name: str) -> bool:
    return f'href="#{name}"' in html or f'id="{name}"' in html


def _is_suppressed(module: ModuleType, class_name: str, member_name: str) -> bool:
    pdoc_config = getattr(module, "__pdoc__", {})
    return pdoc_config.get(f"{class_name}.{member_name}") is False


def _is_private_documentation(value: Any) -> bool:
    doc = inspect.getdoc(value) or ""
    return doc.startswith("@private")


def _public_class_members(module: ModuleType, exported_name: str, value: type[Any]) -> list[str]:
    """Return public methods and fields pdoc should expose for one class."""

    names = set(getattr(value, "__annotations__", {}))
    names.update(name for name in vars(value) if not name.startswith("_"))
    names.update(
        name
        for name, member in inspect.getmembers(value)
        if not name.startswith("_") and (inspect.isroutine(member) or isinstance(member, property))
    )
    return sorted(
        f"{exported_name}.{name}"
        for name in names
        if not _is_suppressed(module, exported_name, name)
        and not _is_private_documentation(getattr(value, name, None))
    )


def _verify_export(module: ModuleType, page_html: str, exported_name: str) -> list[str]:
    errors: list[str] = []
    if not hasattr(module, exported_name):
        return [f"{module.__name__}: __all__ name missing from module: {exported_name}"]

    value = getattr(module, exported_name)
    if not inspect.getdoc(value):
        errors.append(f"{module.__name__}.{exported_name}: missing public API docstring")
    if not _has_anchor(page_html, exported_name):
        errors.append(f"{module.__name__}.{exported_name}: missing generated documentation anchor")

    if not inspect.isclass(value):
        return errors

    for qualified_name in _public_class_members(module, exported_name, value):
        member_name = qualified_name.rsplit(".", 1)[1]
        member = getattr(value, member_name, None)
        if inspect.isroutine(member) and not inspect.getdoc(member):
            errors.append(f"{module.__name__}.{qualified_name}: missing public API docstring")
        if not _has_anchor(page_html, qualified_name):
            errors.append(f"{module.__name__}.{qualified_name}: missing generated documentation anchor")
    return errors


def verify(reference_root: Path = DEFAULT_REFERENCE_ROOT) -> list[str]:
    """Return documentation contract violations for the generated reference."""

    errors: list[str] = []
    for module_name, relative_page in DOCUMENTED_MODULES.items():
        page_path = reference_root / relative_page
        if not page_path.is_file():
            errors.append(f"{module_name}: generated page is missing: {page_path}")
            continue
        page_html = page_path.read_text(encoding="utf-8")
        if not page_html.strip():
            errors.append(f"{module_name}: generated page is empty: {page_path}")
            continue

        module = _load_module(module_name)
        exports = getattr(module, "__all__", ())
        if not exports:
            errors.append(f"{module_name}: documented module has no __all__ declaration")
            continue
        for exported_name in exports:
            errors.extend(_verify_export(module, page_html, exported_name))
    return errors


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        print(f"usage: {Path(sys.argv[0]).name} [reference-directory]", file=sys.stderr)
        return 2
    reference_root = Path(args[0]).resolve() if args else DEFAULT_REFERENCE_ROOT
    errors = verify(reference_root)
    if errors:
        print("Plugin API documentation check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Verified documented plugin API against {reference_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
