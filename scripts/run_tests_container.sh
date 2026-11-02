#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="python:3.14.7-slim-bookworm@sha256:23c59390fc717bf09f9336908199a0ae75d9c4264bf296123f94ad772fea3b52"

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'Docker is required to run the containerized test suite.' >&2
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
        LOG_DIR="${FAT_TEST_LOG_DIR:-/workspace/output/test-logs}"
        mkdir -p "$LOG_DIR"
        failed=0
        phase_results=""

        run_phase() {
            name="$1"
            shift
            log_file="$LOG_DIR/$(printf "%s" "$name" | tr " /" "__").log"
            status_file="$log_file.status"
            rm -f "$status_file"
            printf "\n[%s]\n" "$name"
            (
                set +e
                "$@"
                phase_status=$?
                printf "%s\n" "$phase_status" >"$status_file"
                exit "$phase_status"
            ) 2>&1 | tee "$log_file"
            phase_status=$(cat "$status_file")
            rm -f "$status_file"
            if [ "$phase_status" -eq 0 ]; then
                phase_results="$phase_results\nPASS|$name|$log_file"
            else
                failed=1
                phase_results="$phase_results\nFAIL|$name|$log_file"
                printf "%s\n" "" "--- $name failed; last 40 log lines (max 240 chars each) ---"
                tail -n 40 "$log_file" | cut -c1-240
                printf "%s\n" "--- end $name log ---"
            fi
        }

        run_unit_tests() {
            python -m coverage erase &&
                python -m coverage run --branch -m unittest discover -s tests -p "test_*.py" -v
        }

        run_integration_tests() {
            python -m coverage run --branch -a -m unittest discover -s tests -p "integration_*.py" -v
        }

        verify_docs() {
            ./scripts/build_plugin_api_docs.sh /tmp/plugin-api &&
                python3 scripts/verify_plugin_api_docs.py /tmp/plugin-api &&
                test -s /tmp/plugin-api/index.html &&
                test -s /tmp/plugin-api/plugins/base.html &&
                test -s /tmp/plugin-api/plugins/scraper/base.html &&
                test -s /tmp/plugin-api/plugins/wayback/parsers/api.html &&
                grep -q SurfacePluginBase /tmp/plugin-api/plugins/base.html || return 1

            diff_file="$LOG_DIR/build-and-verify-docs.diff"
            if diff -ru docs/plugin-api/reference /tmp/plugin-api >"$diff_file"; then
                printf "%s\n" "Generated API docs match the committed reference."
                return 0
            fi

            printf "%s\n" "Generated API docs differ; full diff: $diff_file"
            grep "^diff -ru" "$diff_file" || true
            tail -n 40 "$diff_file" | cut -c1-240
            return 1
        }

        run_phase "python-version" \
            python -c "import sys; assert sys.version_info[:3] == (3, 14, 7), sys.version"
        run_phase "install-test-dependencies" \
            python -m pip install \
                --require-hashes \
                --only-binary=:all: \
                --root-user-action=ignore \
                -r requirements-test.txt
        run_phase "unit-tests" \
            run_unit_tests
        run_phase "integration-tests" \
            run_integration_tests
        run_phase "coverage" \
            python -m coverage report --precision=2 --fail-under=80
        run_phase "install-docs-dependencies" \
            python -m pip install \
                --require-hashes \
                --only-binary=:all: \
                --root-user-action=ignore \
                -r requirements-docs.txt
        run_phase "build-and-verify-docs" \
            verify_docs

        printf "\nTest summary\n"
        printf "%b\n" "$phase_results" | while IFS="|" read -r result name log_file; do
            test -n "$result" || continue
            printf "%-5s %s\n" "$result" "$name"
        done
        printf "Logs: %s\n" "$LOG_DIR"
        exit "$failed"
    '
