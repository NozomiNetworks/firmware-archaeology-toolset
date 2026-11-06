from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import pkgutil
import stat
import sys
import types
from pathlib import Path
from typing import Any, Callable, TypeVar


T = TypeVar("T")


def import_module_from_package(package_name: str, module_name: str) -> Any:
    """Import one named module relative to a package namespace."""

    return importlib.import_module(f"{package_name}.{module_name}")


def instantiate_plugin_like(module: Any) -> object | None:
    plugin_class = getattr(module, "PLUGIN_CLASS", None)
    if isinstance(plugin_class, type) and hasattr(plugin_class, "name"):
        return plugin_class()
    return None


def instantiate_subclass(module: Any, exported_name: str, base_class: type[T]) -> T | None:
    candidate = getattr(module, exported_name, None)
    if isinstance(candidate, type) and issubclass(candidate, base_class):
        return candidate()
    return None


def discover_modules(
    package_name: str,
    directory: Path,
    *,
    skip: set[str] | None = None,
    instantiate: Callable[[Any], T | None],
    sort_key: Callable[[T], Any] = lambda item: getattr(item, "name", ""),
) -> list[T]:
    discovered: list[T] = []
    skipped = skip or set()
    for module_info in pkgutil.iter_modules([str(directory)]):
        if module_info.name in skipped:
            continue
        module = import_module_from_package(package_name, module_info.name)
        instance = instantiate(module)
        if instance is not None:
            discovered.append(instance)
    return sorted(discovered, key=sort_key)


def discover_external_modules(root: Path, relative_directory: str, namespace: str) -> list[Any]:
    """Load Python modules from one configured external plugin directory."""

    lexical_root = root.expanduser()
    if lexical_root.is_symlink():
        raise SystemExit(f"External plugin root is not a regular directory: {lexical_root}")
    if lexical_root.exists():
        if not lexical_root.is_dir():
            raise SystemExit(f"External plugin root is not a regular directory: {lexical_root}")
        metadata = lexical_root.stat()
        if metadata.st_uid not in {os.geteuid(), 0} or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise SystemExit(f"External plugin root has unsafe ownership or permissions: {lexical_root}")
    root = lexical_root.resolve()
    directory = (root / relative_directory).resolve()
    try:
        directory.relative_to(root)
    except ValueError as exc:
        raise SystemExit(f"External plugin path escapes the configured root: {directory}") from exc
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise SystemExit(f"External plugin directory is not a directory: {directory}")

    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    package_name = f"_fat_external_{digest}_{namespace.replace('/', '_')}"
    package = types.ModuleType(package_name)
    package.__path__ = [str(directory)]
    package.__package__ = package_name
    sys.modules[package_name] = package

    modules: list[Any] = []
    for module_info in sorted(pkgutil.iter_modules([str(directory)]), key=lambda item: item.name):
        if module_info.name.startswith("_"):
            continue
        module_path = directory / f"{module_info.name}.py"
        if module_info.ispkg:
            raise SystemExit(f"External plugin packages are not supported: {directory / module_info.name}")
        if module_path.is_symlink():
            raise SystemExit(f"Symlinked external plugin is not allowed: {module_path}")
        if not module_path.is_file():
            raise SystemExit(f"External plugin is not a regular file: {module_path}")
        module_name = f"{package_name}.{module_info.name}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise SystemExit(f"Could not load external plugin module: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise SystemExit(f"Failed to load external plugin {module_path}: {exc}") from exc
        modules.append(module)
    return modules
