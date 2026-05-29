"""
Build `data/index.json`, the manifest of available forecast runs.

Scans a data directory for `waves_<run_id>.bin.gz` files (as written by
`grib2bin.py`), reads each one's JSON header, and writes a newest-first list of:

    [{ "id":            "YYYYMMDD_HHMM",
       "file":          "waves_YYYYMMDD_HHMM.bin.gz",
       "forecast_time": "YYYY-MM-DDTHH:MM:SSZ",
       "source":        "NOAA NWPS – ..." }, ...]

The website (`loadData` in `js/app.js`) fetches this to populate the forecast
selector and to resolve which run to display.
"""

import argparse
import gzip
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any

# Filename pattern written by grib2bin.py: waves_<YYYYMMDD_HHMM>.bin.gz
_FILE_RE = re.compile(r"^waves_(\d{8}_\d{4})\.bin\.gz$")


def read_header(path: Path) -> dict[str, Any]:
    """Read the JSON header from a waves_*.bin.gz file (see grib2bin.write_binary)."""
    with gzip.open(path, "rb") as f:
        (header_len,) = struct.unpack("<I", f.read(4))
        header_bytes = f.read(header_len)
    result: dict[str, Any] = json.loads(header_bytes)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Build data/index.json from waves_<id>.bin.gz files.")
    ap.add_argument("--data-dir", type=Path, default=Path("data"), help="Directory of waves_*.bin.gz files")
    ap.add_argument("--out", type=Path, default=None, help="Output path (default: <data-dir>/index.json)")
    args = ap.parse_args()

    data_dir: Path = args.data_dir
    out_path: Path = args.out if args.out else data_dir / "index.json"

    entries: list[dict[str, Any]] = []
    for path in sorted(data_dir.glob("waves_*.bin.gz")):
        m = _FILE_RE.match(path.name)
        if not m:
            continue
        try:
            metadata = read_header(path).get("metadata", {})
        except Exception as e:
            print(f"WARNING: skipping {path.name}: {e}", file=sys.stderr)
            continue
        entries.append(
            {
                "id": m.group(1),
                "file": path.name,
                "forecast_time": metadata.get("forecast_time"),
                "source": metadata.get("source"),
            }
        )

    if not entries:
        sys.exit(f"ERROR: no waves_<id>.bin.gz files found in {data_dir}")

    # Newest first (fall back to the run id if forecast_time is missing).
    entries.sort(key=lambda e: e.get("forecast_time") or e["id"], reverse=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(entries, separators=(",", ":")))
    print(f"Wrote {out_path}  ({len(entries)} forecast(s))")


if __name__ == "__main__":
    main()
