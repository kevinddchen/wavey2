"""
Convert an NWPS GRIB2 file to waves.json for the dive-conditions viewer.

Quick start
-----------
  # 1. List what variables are in the file
  python grib2json.py mtr_nwps_CG3_20260430_1200.grib2 --list

  # 2. Convert
  python grib2json.py mtr_nwps_CG3_20260430_1200.grib2

"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

# ── Variable shortNames ───────────────────────────────────────────────────────

HEIGHT_NAME = "swh"
DIR_NAME = "dirpw"
LEVEL_NAME = "zos"

# ── Types ─────────────────────────────────────────────────────────────────────

Msg = dict[str, Any]
MsgGroup = dict[str, list[Msg]]
MsgResult = tuple[list[Msg], str] | tuple[None, None]

# ── Helpers ──────────────────────────────────────────────────────────────────


def open_file(path: Path) -> Any:
    try:
        import pygrib
    except ImportError:
        sys.exit("ERROR: Run: pip install pygrib")
    return pygrib.open(str(path))


def list_variables(path: Path) -> None:
    grbs = open_file(path)
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for grb in grbs:
        key = (grb.shortName, grb.name, grb.typeOfLevel, grb.level)
        if key not in seen:
            try:
                shape: tuple[Any, ...] = (grb.Nj, grb.Ni)
            except AttributeError:
                shape = (grb.numberOfDataPoints,)
            seen[key] = {"count": 0, "shape": shape}
        seen[key]["count"] += 1
    grbs.close()

    print(f"\nFound {len(seen)} unique variable(s) in {path.name}:\n")
    for (short, name, ltype, level), info in seen.items():
        print(
            f"  shortName={short!r:15s}  steps={info['count']:3d}  "
            f"shape={info['shape']}  typeOfLevel={ltype}  level={level}"
        )
        print(f"    name={name!r}")
    print()


def _best_group(groups: MsgGroup) -> MsgResult:
    """From a dict of shortName→[msgs], return the list with the most entries."""
    if not groups:
        return None, None
    sn = max(groups, key=lambda k: len(groups[k]))
    return sorted(groups[sn], key=lambda m: m["validDate"]), sn


def load_all_messages(path: Path) -> tuple[MsgResult, MsgResult, MsgResult]:
    """Single-pass read; returns (msgs_h, sn_h), (msgs_d, sn_d), (msgs_l, sn_l)."""
    targets: dict[str, MsgGroup] = {
        HEIGHT_NAME.lower(): {},
        DIR_NAME.lower(): {},
        LEVEL_NAME.lower(): {},
    }

    grbs = open_file(path)
    for grb in grbs:
        sn = grb.shortName.lower()
        if sn not in targets:
            continue

        vals = grb.values
        if hasattr(vals, "filled"):
            vals = vals.filled(np.nan)
        lats, lons = grb.latlons()
        entry: Msg = {
            "shortName": grb.shortName,
            "validDate": grb.validDate,
            "analDate": getattr(grb, "analDate", None),
            "values": vals,
            "lats": lats,
            "lons": lons,
        }
        targets[sn].setdefault(sn, []).append(entry)
    grbs.close()

    return (
        _best_group(targets[HEIGHT_NAME.lower()]),
        _best_group(targets[DIR_NAME.lower()]),
        _best_group(targets[LEVEL_NAME.lower()]),
    )


def extract(
    msgs: list[Msg],
) -> tuple[list[str], npt.NDArray[Any], npt.NDArray[Any], npt.NDArray[Any], str | None]:
    """Return (times_iso, lat_1d, lon_1d, values[nt,ny,nx], ref_time_iso)."""
    lats2d = msgs[0]["lats"]
    lons2d = msgs[0]["lons"]

    data = np.stack([m["values"] for m in msgs], axis=0)  # [nt, ny, nx]

    times_iso = [m["validDate"].strftime("%Y-%m-%dT%H:%M:%SZ") for m in msgs]
    ref = msgs[0]["analDate"]
    ref_time_iso = ref.strftime("%Y-%m-%dT%H:%M:%SZ") if ref else None

    # Reduce 2D lat/lon grids to 1D axes
    if lats2d.ndim == 2:
        lat = lats2d[:, 0]
        lon = lons2d[0, :]
    else:
        lat = lats2d.ravel()
        lon = lons2d.ravel()

    # Ensure lat increases S→N
    if lat[0] > lat[-1]:
        lat = lat[::-1]
        data = data[:, ::-1, :]

    # Normalize 0-360 → -180..180
    if lon.max() > 180:
        lon = np.where(lon > 180, lon - 360, lon)
        order = np.argsort(lon)
        lon = lon[order]
        data = data[:, :, order]

    return times_iso, lat, lon, data, ref_time_iso


def to_grid_list(arr: npt.NDArray[Any], ndecimals: int) -> list[list[float | None]]:
    """[nt,ny,nx] → list[ny*nx] of list[nt] values; NaN/Inf → null."""
    nt, ny, nx = arr.shape
    out: list[list[float | None]] = []
    for y in range(ny):
        for x in range(nx):
            row: list[float | None] = []
            for v in arr[:, y, x]:
                if np.isnan(v) or np.isinf(v):
                    row.append(None)
                else:
                    row.append(round(float(v), ndecimals))
            out.append(row)
    return out


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    # fmt: off
    ap = argparse.ArgumentParser(
        description="Convert NWPS GRIB2 → waves.json for the dive conditions viewer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("input", help="Input .grib2 file")
    ap.add_argument("--list", action="store_true",
                    help="List available variables and exit")
    ap.add_argument("--out", default="data/waves.json",
                    help="Output path (default: data/waves.json)")
    ap.add_argument("--round", type=int, default=2, metavar="N",
                    help="Decimal places to round values to (default: 2)")
    ap.add_argument("--step", type=int, default=1, metavar="N",
                    help="Keep every Nth time step, e.g. --step 3 for 3-hourly (default: 1)")
    args = ap.parse_args()
    # fmt: on

    path = Path(args.input)
    if not path.exists():
        sys.exit(f"ERROR: File not found: {path}")

    if args.list:
        list_variables(path)
        return

    print("Reading GRIB2 messages...")
    (msgs_h, sn_h), (msgs_d, sn_d), (msgs_l, sn_l) = load_all_messages(path)

    if msgs_h is None:
        print(f"ERROR: Wave height variable '{HEIGHT_NAME}' not found. Run --list to see available variables.")
        sys.exit(1)

    print(f"  wave_height  → {sn_h} ({len(msgs_h)} steps)")
    if sn_d and msgs_d:
        print(f"  wave_dir     → {sn_d} ({len(msgs_d)} steps)")
    else:
        print("  wave_dir     → NOT FOUND (will be null)")
    if sn_l and msgs_l:
        print(f"  water_level  → {sn_l} ({len(msgs_l)} steps)")
    else:
        print("  water_level  → NOT FOUND (will be null)")

    times_iso, lats, lons, arr_h, ref_time = extract(msgs_h)
    if args.step > 1:
        arr_h = arr_h[:: args.step]
        times_iso = times_iso[:: args.step]
    nt, ny, nx = arr_h.shape

    print(f"\n  Grid: {nx}×{ny},  {nt} time steps")
    print(f"  Lat:  {lats[0]:.4f} → {lats[-1]:.4f}")
    print(f"  Lon:  {lons[0]:.4f} → {lons[-1]:.4f}")

    grid = {
        "nx": int(nx),
        "ny": int(ny),
        "lat_min": float(lats[0]),
        "lat_max": float(lats[-1]),
        "lon_min": float(lons[0]),
        "lon_max": float(lons[-1]),
    }

    out: dict[str, Any] = {
        "metadata": {
            "source": f"NOAA NWPS – {path.name}",
            "forecast_time": ref_time or times_iso[0],
            "times": times_iso,
            "grid": grid,
            "units": {"wave_height": "m", "wave_dir": "°", "water_level": "m"},
        },
        "wave_height": to_grid_list(arr_h, args.round),
    }

    if msgs_d:
        _, _, _, arr_d, _ = extract(msgs_d)
        out["wave_dir"] = to_grid_list(arr_d[:: args.step], 0)
    else:
        out["wave_dir"] = [[None] * nt] * (ny * nx)

    if msgs_l:
        _, _, _, arr_l, _ = extract(msgs_l)
        out["water_level"] = to_grid_list(arr_l[:: args.step], args.round)
    else:
        out["water_level"] = None

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, separators=(",", ":"))

    size_mb = out_path.stat().st_size / 1e6
    print(f"\nWrote {out_path}  ({size_mb:.1f} MB)")
    if size_mb > 20:
        print("  Tip: large file — consider downsampling the grid before converting.")


if __name__ == "__main__":
    main()
