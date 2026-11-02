#!/usr/bin/env bash

set -uo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="python:3.14.7-slim-bookworm@sha256:23c59390fc717bf09f9336908199a0ae75d9c4264bf296123f94ad772fea3b52"
failed=0

step() {
    name="$1"
    shift
    printf '\n[%s]\n' "$name"
    if "$@"; then
        printf 'PASS  %s\n' "$name"
    else
        printf 'FAIL  %s\n' "$name"
        failed=1
    fi
}

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'Docker is required to prepare a pull request.' >&2
    exit 1
fi

regenerate_docs() {
    docker run --rm \
        --platform linux/amd64 \
        -e PYTHONDONTWRITEBYTECODE=1 \
        -v "${REPO_ROOT}:/workspace" \
        -w /workspace \
        --entrypoint sh \
        "${IMAGE}" \
        -ceu '
            python -m pip install \
                --require-hashes \
                --only-binary=:all: \
                --root-user-action=ignore \
                -r requirements-docs.txt
            ./scripts/build_plugin_api_docs.sh
        '
}

check_whitespace() {
    git --no-pager diff --check && git --no-pager diff --cached --check
}

cd "${REPO_ROOT}"

step "Regenerate API documentation" regenerate_docs
step "Check tracked-file whitespace" check_whitespace
step "Run local CI" ./scripts/run_tests_container.sh
step "Run browser integration CI" ./scripts/run_browser_tests_container.sh

printf '\nPR preparation summary\n'
if [ "$failed" -eq 0 ]; then
    printf '%s\n' 'PASS  Local quality gate'
    if [ -n "$(git status --porcelain)" ]; then
        printf '%s\n' 'WARN  Checks passed, but changes still need to be committed before opening the PR.'
    else
        printf '%s\n' 'READY The branch is ready to open a PR.'
    fi
else
    printf '%s\n' 'FAIL  The branch is not ready to open a PR.'
fi

printf '\nGit status\n'
git status --short

exit "$failed"
