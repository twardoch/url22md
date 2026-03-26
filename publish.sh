#!/usr/bin/env bash
# this_file: publish.sh
# Publish url22md: install, clean, bump version, build, upload to PyPI.
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Installing package (editable)..."
uv pip install --system --upgrade -e .

echo "==> Cleaning previous builds..."
uvx hatch clean

echo "==> Bumping version via gitnextver..."
gitnextver

echo "==> Building sdist + wheel..."
uvx hatch build

echo "==> Publishing to PyPI..."
uv publish

echo "==> Done. Published $(uvx hatch version)"
