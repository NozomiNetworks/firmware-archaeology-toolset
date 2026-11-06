#!/usr/bin/env python3
"""Install FAT's locked development dependencies and optional browser runtime."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = {
    "test": ROOT / "requirements-test.txt",
    "docs": ROOT / "requirements-docs.txt",
    "browser": ROOT / "requirements-browser.txt",
}


def install_requirements(groups: list[str]) -> None:
    for group in groups:
        requirement_file = REQUIREMENTS[group]
        print(f"Installing locked {group} dependencies from {requirement_file.name}...")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--only-binary=:all:",
                "--root-user-action=ignore",
                "-r",
                str(requirement_file),
            ],
            check=True,
        )


def install_browser(*, system_deps: bool) -> None:
    command = [sys.executable, "-m", "playwright", "install"]
    if system_deps:
        command.append("--with-deps")
    command.append("chromium")
    print("Installing the Chromium runtime selected by the pinned Playwright package...")
    subprocess.run(command, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="install locked test dependencies")
    parser.add_argument("--docs", action="store_true", help="install locked documentation dependencies")
    parser.add_argument("--browser", action="store_true", help="install locked browser tooling and Chromium")
    parser.add_argument(
        "--system-deps",
        action="store_true",
        help="install Playwright's browser system dependencies as well",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    groups = [
        name
        for name, selected in (
            ("test", args.test),
            ("docs", args.docs),
            ("browser", args.browser),
        )
        if selected
    ]
    if not groups:
        groups = list(REQUIREMENTS)
    if args.system_deps and "browser" not in groups:
        raise SystemExit("--system-deps requires --browser")
    install_requirements(groups)
    if "browser" in groups:
        install_browser(system_deps=args.system_deps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
