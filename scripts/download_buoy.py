"""
Download NDBC buoy observations and write them as JSON for the dive-conditions viewer.

NDBC publishes real-time observations as a whitespace-delimited text table at
`https://www.ndbc.noaa.gov/data/realtime2/<id>.txt`. The first two lines are
headers (column names, then units), and `MM` marks a missing value. Example:

    #YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
    #yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
    2026 05 30 17 56  MM   MM   MM   1.5    13   7.5 279     MM    MM  13.0    MM   MM   MM    MM

We keep the wave columns the website charts: WVHT (significant wave height, m),
DPD (dominant wave period, s), and MWD (mean wave direction, degrees true, the
direction the waves are coming *from*). Observations within the last 5 days are
written to `data/buoy_<id>_<YYYYMMDD_HHMM>.json` — the timestamp (the latest
observation's hour) is part of the filename so a refreshed file gets a new URL
and can't be served stale from a browser/CDN cache; `build_index.py` records the
current filename in `data/index.json`. The payload is parallel arrays (one shared
timestamp list plus one list per variable) so repeated JSON keys aren't stored
per reading:

    {
      "name": "Buoy 46236 Measurements",
      "units":       { "wave_height": "m", "wave_period": "s", "wave_dir": "deg" },
      "times":       ["2026-05-25T18:00:00Z", ...],
      "wave_height": [1.4, ...],
      "wave_period": [13, ...],
      "wave_dir":    [286, ...]
    }

The per-variable arrays are aligned to `times` (`null` marks a missing reading).
`wave_height` stays in meters and `wave_dir` stays as the observed "direction-from",
matching the raw NWPS forecast fields — the website applies its own unit scaling
and the +180° "direction-toward" convention to both (see `initData` / `initBuoy`
in `js/app.js`). Missing values become `null`.
"""

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

LOG = logging.getLogger(__name__)

# Known buoys
BUOYS = ("46236", "46239")

_BASE_URL = "https://www.ndbc.noaa.gov/data/realtime2"

# Column index (after the 5 date/time columns) → output field. NDBC realtime2
# layout is fixed: YY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD ...
_COLUMNS = {
    "wave_height": 8,  # WVHT (m)
    "wave_period": 9,  # DPD  (s)
    "wave_dir": 11,  # MWD  (deg true, direction-from)
}

_MISSING = "MM"


def _parse_value(token: str) -> float | None:
    """Parse an NDBC numeric token, mapping the `MM` sentinel (and junk) to None."""
    if token == _MISSING:
        return None
    return float(token)


def parse_observations(text: str, since: datetime | None) -> dict[str, list[Any]]:
    """
    Parse the NDBC realtime2 table, keeping wave fields for rows at/after `since`.

    The buoy reports a few times per hour; readings are thinned to one per hour
    (the one nearest the top of the hour) since the website's charts only resolve
    hourly. Returns parallel, oldest-first arrays: `times` plus one list per
    variable in `_COLUMNS` (aligned to `times`, `None` for a missing reading).
    Rows that can't be parsed (short lines, bad timestamps) are skipped.
    """
    # Keep, per clock hour, the (distance-to-the-hour, row) closest to HH:00.
    by_hour: dict[datetime, tuple[float, dict[str, Any]]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue  # header / comment lines

        fields = line.split()
        assert len(fields) > max(_COLUMNS.values())

        year, month, day, hour, minute = (int(fields[i]) for i in range(5))
        when = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        if since is not None and when < since:
            continue

        slot = when.replace(minute=0, second=0, microsecond=0)
        if when.minute >= 30:
            slot += timedelta(hours=1)
        distance = abs((when - slot).total_seconds())
        if slot not in by_hour or distance < by_hour[slot][0]:
            by_hour[slot] = (
                distance,
                {name: _parse_value(fields[idx]) for name, idx in _COLUMNS.items()},
            )

    # Saved timestamps are the rounded hour (`slot`), not the raw observation time.
    items = sorted(by_hour.items())
    columns = {"times": [slot.strftime("%Y-%m-%dT%H:%M:%SZ") for slot, _ in items]}
    for name in _COLUMNS:
        columns[name] = [row[name] for _, (_, row) in items]
    return columns


def download_buoy(buoy_id: str, out_dir: Path, lookback_days: int | None = None) -> Path:
    """
    Download buoy `buoy_id` observations and write `data/buoy_<id>_<stamp>.json`.

    The filename's `<stamp>` (YYYYMMDD_HHMM) is the latest observation's hour, so a
    refreshed file gets a distinct URL and won't be served stale from cache.

    Args:
        buoy_id: NDBC station id (must be a key in `BUOYS`).
        out_dir: Directory to write the JSON file into.
        lookback_days: Keep observations from the last this many days.

    Returns:
        Path to the written JSON file.

    Raises:
        KeyError: If `buoy_id` is not in `BUOYS`.
        HTTPError: If the NDBC request fails.
    """

    url = f"{_BASE_URL}/{buoy_id}.txt"

    r = requests.get(url)
    r.raise_for_status()

    since = datetime.now(timezone.utc) - timedelta(days=lookback_days) if lookback_days is not None else None
    columns = parse_observations(r.text, since)
    n = len(columns["times"])
    LOG.info(f"Parsed {n} observation(s) from '{url}' (last {lookback_days} days)")

    payload = {
        "name": f"Buoy {buoy_id} Measurements",
        "units": {"wave_height": "m", "wave_period": "s", "wave_dir": "deg"},
        **columns,
    }

    # Stamp the filename with the latest observation hour (or now, if none) so the
    # URL changes whenever the data does.
    latest = columns["times"][-1] if columns["times"] else None
    stamp = datetime.strptime(latest, "%Y-%m-%dT%H:%M:%SZ") if latest else datetime.now(timezone.utc)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"buoy_{buoy_id}_{stamp.strftime('%Y%m%d_%H%M')}.json"
    out_path.write_text(json.dumps(payload, separators=(",", ":")))
    LOG.info(f"Wrote '{out_path}' ({n} observations)")
    return out_path


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    ap = argparse.ArgumentParser(
        description="Download NDBC buoy observations as JSON for the dive conditions viewer.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # fmt: off
    ap.add_argument(
        "--buoy-id", "-b", required=True, choices=sorted(BUOYS), help="NDBC station id",
    )
    ap.add_argument(
        "--out-dir", "-o", type=Path, default=Path("./data/"), help="Output directory to save the .json file",
    )
    ap.add_argument(
        "--lookback-days",
        "-d",
        type=int,
        default=5,
        help="Keep observations from the last N days",
    )
    # fmt: on
    args = ap.parse_args()

    download_buoy(buoy_id=args.buoy_id, out_dir=args.out_dir, lookback_days=args.lookback_days)


if __name__ == "__main__":
    main()
