#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob  # an empty gribs/ glob expands to nothing, not a literal path

# `uv run` syncs the environment before each command, which would reinstall the dev group into an
# environment deliberately built without it (`uv sync --no-dev`). Runtime deps are still installed.
export UV_NO_DEV=1

DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$DIR"

DATA="data"
GRIBS="gribs"

rm -rf "$DATA" "$GRIBS"
mkdir -p "$DATA" "$GRIBS"

# Download forecast data
uv run -m wavey2.apps.download_grib --out-dir "$GRIBS"

# Convert GRIB2 files to our custom binary format
for f in "$GRIBS"/*.grib2; do
    uv run -m wavey2.apps.grib2bin "$f" --out-dir "$DATA"
done

# Download buoy data
for id in 46236 46239; do
    uv run -m wavey2.apps.download_buoy "$id" --out-dir "$DATA"
done

# Build index of forecast + buoy data
uv run -m wavey2.apps.build_index --data-dir "$DATA"
