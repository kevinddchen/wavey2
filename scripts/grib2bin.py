"""
Convert an NWPS GRIB2 file to waves.bin.gz for the dive-conditions viewer.

Output format (gzip-compressed)
-------------------------------
  [4 bytes : little-endian u32]     header byte length (includes padding)
  [N bytes : UTF-8 JSON]            header (padded with spaces so the binary
                                    payload starts on a 4-byte boundary)
  [binary  : little-endian int16]   one array per variable, in `header.variables`
                                    order, each of length ncells * nt (cell-major,
                                    time-minor); INT16_MIN (-32768) means null.

The whole stream is wrapped in gzip on disk (`waves.bin.gz`) because GitHub
Pages doesn't auto-compress `application/octet-stream`; the browser
decompresses it via `DecompressionStream` before decoding.

Real values are recovered as `int_value / scale + offset` (scale/offset live in
the header per variable).

Quick start
-----------
  python grib2bin.py mtr_nwps_CG3_20260430_1200.grib2 --list
  python grib2bin.py mtr_nwps_CG3_20260430_1200.grib2

"""

import argparse
import gzip
import json
import struct
import sys
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

# ── Variable shortNames ───────────────────────────────────────────────────────

HEIGHT_NAME = "swh"
DIR_NAME = "dirpw"
PERIOD_NAME = "perpw"
LEVEL_NAME = "zos"

# ── Binary format ────────────────────────────────────────────────────────────

SENTINEL = -32768  # INT16_MIN — reserved for null
INT16_MAX = 32767

# Per-variable quantization. Scale is integer counts per unit; precision is 1/scale.
# Ranges chosen to comfortably fit observed values within int16 (±32767).
#   wave_height (m): 0–327 m at 0.01 m precision
#   wave_dir    (°): 0–360 ° at 1 ° precision
#   wave_period (s): 0–327 s at 0.01 s precision
#   water_level (m): ±327 m at 0.01 m precision
QUANT: dict[str, dict[str, float]] = {
    "wave_height": {"scale": 100, "offset": 0.0},
    "wave_dir": {"scale": 1, "offset": 0.0},
    "wave_period": {"scale": 100, "offset": 0.0},
    "water_level": {"scale": 100, "offset": 0.0},
}

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


def load_all_messages(path: Path) -> tuple[MsgResult, MsgResult, MsgResult, MsgResult]:
    """Single-pass read; returns (msgs_h, sn_h), (msgs_d, sn_d), (msgs_p, sn_p), (msgs_l, sn_l)."""
    targets: dict[str, MsgGroup] = {
        HEIGHT_NAME.lower(): {},
        DIR_NAME.lower(): {},
        PERIOD_NAME.lower(): {},
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
        _best_group(targets[PERIOD_NAME.lower()]),
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


def quantize_int16(arr: npt.NDArray[Any], scale: float, offset: float) -> npt.NDArray[np.int16]:
    """[nt,ny,nx] → int16 [ny*nx, nt]; NaN/Inf → SENTINEL, out-of-range clipped."""
    nt, ny, nx = arr.shape
    # Cell-major, time-minor: result[y*nx + x, t] = arr[t, y, x]
    flat = np.transpose(arr, (1, 2, 0)).reshape(ny * nx, nt)
    nan_mask = ~np.isfinite(flat)
    q = np.where(nan_mask, 0.0, np.round((flat - offset) * scale))
    np.clip(q, SENTINEL + 1, INT16_MAX, out=q)
    q[nan_mask] = SENTINEL
    return q.astype(np.int16)


def write_binary(
    out_path: Path,
    metadata: dict[str, Any],
    arrays: list[tuple[str, npt.NDArray[np.int16]]],
) -> None:
    """Write [u32 header_len][JSON header][int16 arrays], gzip-compressed."""
    ncells, nt = arrays[0][1].shape
    for name, arr in arrays:
        assert arr.dtype == np.int16, f"{name} must be int16, got {arr.dtype}"
        assert arr.shape == (ncells, nt), f"{name} shape mismatch: {arr.shape}"

    header = {
        "version": 1,
        "metadata": metadata,
        "ncells": int(ncells),
        "nt": int(nt),
        "variables": [
            {
                "name": name,
                "dtype": "int16",
                "scale": QUANT[name]["scale"],
                "offset": QUANT[name]["offset"],
                "sentinel": SENTINEL,
            }
            for name, _ in arrays
        ],
    }

    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    # Pad so the binary payload starts on a 4-byte boundary
    pad = (-len(header_bytes)) % 4
    header_bytes += b" " * pad

    with gzip.open(out_path, "wb", compresslevel=9) as f:
        f.write(struct.pack("<I", len(header_bytes)))
        f.write(header_bytes)
        for _, arr in arrays:
            f.write(arr.astype("<i2", copy=False).tobytes())


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    # fmt: off
    ap = argparse.ArgumentParser(
        description="Convert NWPS GRIB2 → waves.bin for the dive conditions viewer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("input", help="Input .grib2 file")
    ap.add_argument("--list", action="store_true",
                    help="List available variables and exit")
    ap.add_argument("--out", default="data/waves.bin.gz",
                    help="Output path (default: data/waves.bin.gz)")
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
    (msgs_h, sn_h), (msgs_d, sn_d), (msgs_p, sn_p), (msgs_l, sn_l) = load_all_messages(path)

    if msgs_h is None:
        print(f"ERROR: Wave height variable '{HEIGHT_NAME}' not found. Run --list to see available variables.")
        sys.exit(1)

    print(f"  wave_height  → {sn_h} ({len(msgs_h)} steps)")
    if sn_d and msgs_d:
        print(f"  wave_dir     → {sn_d} ({len(msgs_d)} steps)")
    else:
        print("  wave_dir     → NOT FOUND (will be null)")
    if sn_p and msgs_p:
        print(f"  wave_period  → {sn_p} ({len(msgs_p)} steps)")
    else:
        print("  wave_period  → NOT FOUND (will be null)")
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

    metadata: dict[str, Any] = {
        "source": f"NOAA NWPS – {path.name}",
        "forecast_time": ref_time or times_iso[0],
        "times": times_iso,
        "grid": grid,
        "units": {"wave_height": "m", "wave_dir": "°", "wave_period": "s", "water_level": "m"},
    }

    arrays: list[tuple[str, npt.NDArray[np.int16]]] = []
    arrays.append(("wave_height", quantize_int16(arr_h, **QUANT["wave_height"])))

    if msgs_d:
        _, _, _, arr_d, _ = extract(msgs_d)
        arr_d = arr_d[:: args.step]
        arrays.append(("wave_dir", quantize_int16(arr_d, **QUANT["wave_dir"])))
    else:
        arrays.append(("wave_dir", np.full((ny * nx, nt), SENTINEL, dtype=np.int16)))

    if msgs_p:
        _, _, _, arr_p, _ = extract(msgs_p)
        arr_p = arr_p[:: args.step]
        arrays.append(("wave_period", quantize_int16(arr_p, **QUANT["wave_period"])))
    else:
        arrays.append(("wave_period", np.full((ny * nx, nt), SENTINEL, dtype=np.int16)))

    if msgs_l:
        _, _, _, arr_l, _ = extract(msgs_l)
        arr_l = arr_l[:: args.step]
        arrays.append(("water_level", quantize_int16(arr_l, **QUANT["water_level"])))
    else:
        arrays.append(("water_level", np.full((ny * nx, nt), SENTINEL, dtype=np.int16)))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_binary(out_path, metadata, arrays)

    size_mb = out_path.stat().st_size / 1e6
    print(f"\nWrote {out_path}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
