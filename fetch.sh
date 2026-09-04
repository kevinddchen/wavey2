#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob  # an empty gribs/ glob expands to nothing, not a literal path

DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$DIR"

DATA="data"
GRIBS="gribs"

rm -rf "$DATA" "$GRIBS"
mkdir -p "$DATA" "$GRIBS"
uv run -m wavey2.apps.download_grib --out-dir "$GRIBS"
for f in "$GRIBS"/*.grib2; do
    uv run -m wavey2.apps.grib2bin "$f" --out-dir "$DATA"
done
for id in 46236 46239; do
    uv run -m wavey2.apps.download_buoy "$id" --out-dir "$DATA"
done
uv run -m wavey2.apps.build_index --data-dir "$DATA"
