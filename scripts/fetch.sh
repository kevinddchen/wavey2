#!/usr/bin/env bash
# Download forecast GRIB2 files into gribs/. Run `scripts/build.sh` afterwards to turn them into data/.
set -euo pipefail

# `uv run` syncs the environment before each command, which would reinstall the dev group into an
# environment deliberately built without it (`uv sync --no-dev`). Runtime deps are still installed.
export UV_NO_DEV=1

ROOT=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd )
cd "$ROOT"

GRIBS="gribs"

# Files already in gribs/ are kept, so this only downloads what has been published since
uv run -m wavey2.apps.download_grib --out-dir "$GRIBS" --keep 10
