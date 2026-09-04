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

import gzip
import json
import logging
import re
import struct
from pathlib import Path
from typing import Any

import tyro

from wavey2.header import Header
from wavey2.logging import setup_logging

LOG = logging.getLogger(Path(__file__).stem)

# Filename patterns written by grib2bin.py / download_buoy.py.
_WAVES_RE = re.compile(r"^waves_(\d{8}_\d{4})\.bin\.gz$")
_BUOY_RE = re.compile(r"^buoy_(\w+)_(\d{8}_\d{4})\.json$")


def read_header(path: Path) -> Header:
    """Read the JSON header from a waves_*.bin.gz file (see grib2bin.write_binary)."""
    with gzip.open(path, "rb") as f:
        (header_len,) = struct.unpack("<I", f.read(4))
        header_bytes = f.read(header_len)
    return Header.model_validate_json(header_bytes)


def main(
    data_dir: Path = Path("./data/"),
    out_path: Path | None = None,
) -> None:
    """
    Build data/index.json from waves_<id>.bin.gz files.

    Args:
        data_dir: Directory of waves_*.bin.gz files.
        out_path: Output path. If none, defaults to <data-dir>/index.json.
    """

    out_path = out_path or data_dir / "index.json"

    forecasts: list[dict[str, Any]] = []
    for path in sorted(data_dir.glob("waves_*.bin.gz")):
        m = _WAVES_RE.match(path.name)
        if not m:
            continue
        metadata = read_header(path).metadata
        forecasts.append(
            {
                "id": m.group(1),
                "file": path.name,
                "forecast_time": metadata.forecast_time,
                "source": metadata.source,
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
    setup_logging()
    tyro.cli(main)
