#!/usr/bin/env bash
# Save every *-review.json file in a directory through the sussed CLI.

set -u
set -o pipefail

usage() {
  cat <<'USAGE'
Usage: batch_save.sh <reviews-dir>

Run this from the sussed Python project directory, the one containing:
  pyproject.toml
  src/sussed/

For each <prefix>-review.json file, the script runs:
  uv run sussed review save <prefix> --input <file>
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -ne 1 ]]; then
  usage >&2
  exit 2
fi

reviews_dir=$1

if [[ ! -f pyproject.toml || ! -d src/sussed ]]; then
  echo "❌ Run this from the sussed Python project directory (contains pyproject.toml and src/sussed)." >&2
  exit 2
fi

if [[ ! -d "$reviews_dir" ]]; then
  echo "❌ Reviews directory not found: $reviews_dir" >&2
  exit 2
fi

shopt -s nullglob
review_files=("$reviews_dir"/*-review.json)
shopt -u nullglob

if [[ ${#review_files[@]} -eq 0 ]]; then
  echo "❌ No *-review.json files found in: $reviews_dir" >&2
  exit 1
fi

saved=0
failed=0

for review_file in "${review_files[@]}"; do
  base=$(basename "$review_file")
  stem=${base%-review.json}
  prefix=${stem:0:8}

  output=$(uv run sussed review save "$prefix" --input "$review_file" 2>&1)
  status=$?

  if [[ $status -eq 0 ]]; then
    echo "✓ $prefix saved ($review_file)"
    saved=$((saved + 1))
  else
    echo "❌ $prefix failed ($review_file)"
    if [[ -n "$output" ]]; then
      printf '%s\n' "$output" | sed 's/^/  /'
    fi
    failed=$((failed + 1))
  fi
done

echo "Done: $saved saved, $failed failed"

if [[ $failed -gt 0 ]]; then
  exit 1
fi
