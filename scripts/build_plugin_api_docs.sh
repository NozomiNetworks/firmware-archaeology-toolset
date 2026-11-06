#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOLUTION_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${1:-${SOLUTION_DIR}/docs/plugin-api/reference}"

if ! python3 -m pdoc --version >/dev/null 2>&1; then
  echo "pdoc is not installed." >&2
  echo "Install docs dependencies with: python3 -m pip install -r ${SOLUTION_DIR}/requirements-docs.txt" >&2
  exit 1
fi

rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}"

cd "${SOLUTION_DIR}"
API_MODULES_OUTPUT="$(
  PYTHONPATH="${SOLUTION_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
    python3 -c 'from scripts.verify_plugin_api_docs import DOCUMENTED_MODULES; print("\n".join(DOCUMENTED_MODULES))'
)"
API_MODULES=()
while IFS= read -r module; do
  if [[ -n "${module}" ]]; then
    API_MODULES+=("${module}")
  fi
done <<< "${API_MODULES_OUTPUT}"
PYTHONPATH="${SOLUTION_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
  python3 -m pdoc \
    --output-directory "${OUTPUT_DIR}" \
    --template-directory "${SOLUTION_DIR}/docs/plugin-api/templates" \
    "${API_MODULES[@]}"

python3 - "${OUTPUT_DIR}" <<'PY'
from pathlib import Path
import sys

output_dir = Path(sys.argv[1])
for path in output_dir.rglob("*"):
    if not path.is_file() or path.suffix not in {".html", ".js"}:
        continue
    text = path.read_text(encoding="utf-8")
    cleaned = "\n".join(line.rstrip() for line in text.splitlines()) + "\n"
    if cleaned != text:
        path.write_text(cleaned, encoding="utf-8")
PY

echo "Generated plugin API docs in ${OUTPUT_DIR}"
