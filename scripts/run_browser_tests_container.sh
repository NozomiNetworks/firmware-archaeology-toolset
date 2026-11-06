#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="python:3.14.7-slim-bookworm@sha256:23c59390fc717bf09f9336908199a0ae75d9c4264bf296123f94ad772fea3b52"

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'Docker is required to run browser integration checks.' >&2
    exit 1
fi

exec docker run --rm \
    --platform linux/amd64 \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e PYTHONUNBUFFERED=1 \
    -v "${REPO_ROOT}:/workspace" \
    -w /workspace \
    --entrypoint sh \
    "${IMAGE}" \
    -ceu '
        ./scripts/install_dependencies.sh --browser --system-deps
        python -m unittest discover -s tests -p "browser_integration.py" -v
    '
