"""
Build `data/index.json`, the manifest of available forecast runs and buoys.

Scans a data directory for `waves_<run_id>.bin.gz` files (as written by
`grib2bin.py`) and `buoy_<id>_<stamp>.json` files (as written by
`download_buoy.py`), and writes:

    { "forecasts": [{ "id":            "YYYYMMDD_HHMM",
                      "file":          "waves_YYYYMMDD_HHMM.bin.gz",
                      "forecast_time": "YYYY-MM-DDTHH:MM:SSZ",
                      "source":        "NOAA NWPS – ..." }, ...],
      "buoys":     [{ "id":   "46236",
                      "file": "buoy_46236_YYYYMMDD_HHMM.json" }, ...] }

Forecasts are newest-first. Buoy filenames are timestamped (for cache-busting),
so the website can't hardcode them — it reads the current filename here. Only the
newest file per buoy id is listed. The website (`loadData` in `js/app.js`) fetches
this to populate the forecast selector and to resolve which files to load.
"""

import argparse
import gzip
import json
import logging
import re
import struct
from pathlib import Path
from typing import Any

LOG = logging.getLogger(Path(__file__).stem)

# Filename patterns written by grib2bin.py / download_buoy.py.
_WAVES_RE = re.compile(r"^waves_(\d{8}_\d{4})\.bin\.gz$")
_BUOY_RE = re.compile(r"^buoy_(\w+)_(\d{8}_\d{4})\.json$")


def read_header(path: Path) -> dict[str, Any]:
    """Read the JSON header from a waves_*.bin.gz file (see grib2bin.write_binary)."""
    with gzip.open(path, "rb") as f:
        (header_len,) = struct.unpack("<I", f.read(4))
        header_bytes = f.read(header_len)
    result: dict[str, Any] = json.loads(header_bytes)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build data/index.json from waves_<id>.bin.gz files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # fmt: off
    ap.add_argument(
        "--dir", "-d", type=Path, default=Path("./data/"), help="Directory of waves_*.bin.gz files",
    )
    ap.add_argument(
        "--out", type=Path, default=None, help="Output path. If none, defaults to <data-dir>/index.json",
    )
    # fmt: on
    args = ap.parse_args()

    data_dir: Path = args.dir
    out_path: Path = args.out or data_dir / "index.json"

    forecasts: list[dict[str, Any]] = []
    for path in sorted(data_dir.glob("waves_*.bin.gz")):
        m = _WAVES_RE.match(path.name)
        if not m:
            continue
        try:
            metadata = read_header(path).get("metadata", {})
        except Exception as e:
            LOG.warning(f"WARNING: skipping {path.name}: {e}")
            continue
        forecasts.append(
            {
                "id": m.group(1),
                "file": path.name,
                "forecast_time": metadata.get("forecast_time"),
                "source": metadata.get("source"),
            }
        )

    if not forecasts:
        raise FileNotFoundError(f"no waves_<id>.bin.gz files found in {data_dir}")

    # Newest first
    forecasts.sort(key=lambda e: e["forecast_time"], reverse=True)

    # Keep only the newest file per buoy id (filenames sort by their timestamp).
    buoy_files: dict[str, str] = {}
    for path in sorted(data_dir.glob("buoy_*.json")):
        m = _BUOY_RE.match(path.name)
        if m:
            buoy_files[m.group(1)] = path.name  # later (newer stamp) wins
    buoys = [{"id": buoy_id, "file": file} for buoy_id, file in sorted(buoy_files.items())]

    index = {"forecasts": forecasts, "buoys": buoys}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(index, separators=(",", ":")))
    LOG.info(f"Wrote '{out_path}' ({len(forecasts)} forecasts, {len(buoys)} buoys)")


if __name__ == "__main__":
    from wavey2.logging import setup_logging

    setup_logging()
    main()
