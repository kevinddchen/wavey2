"""
Convert an NWPS GRIB2 file to waves.bin.gz for the dive-conditions viewer.

Output format (gzip-compressed)
-------------------------------
  [4 bytes : little-endian u32]     header byte length (includes padding)
  [N bytes : UTF-8 JSON]            header (padded with spaces so the binary
                                    payload starts on a 4-byte boundary)
  [binary  : little-endian typed]   one array per variable, in `header.variables`
                                    order, each of length `ncells * nt`. Layout
                                    is cell-major / time-minor: the value at
                                    cell `c`, time `t` is at index `c * nt + t`.
                                    dtype and sentinel value are declared per
                                    variable in the header.

The whole stream is wrapped in gzip on disk (`waves.bin.gz`); the browser
decompresses it via `DecompressionStream` before decoding.

JSON header schema
------------------
  {
    "version": 1,                    // bump when the layout changes
    "metadata": {
      "source":        "NOAA NWPS – mtr_nwps_CG3_YYYYMMDD_HH00.grib2",
      "forecast_time": "YYYY-MM-DDTHH:MM:SSZ",   // GRIB2 analysis time (UTC)
      "times":         ["YYYY-MM-DDTHH:MM:SSZ", ...],  // one per timestep, length = nt
      "grid":          { "nx": int, "ny": int,
                         "lat_min": float, "lat_max": float,
                         "lon_min": float, "lon_max": float },
      "units":         { "wave_height": "m", "wave_dir": "deg",
                         "wave_period": "s", "water_level": "m" }
    },
    "ncells": int,                   // = grid.nx * grid.ny
    "nt":     int,                   // number of timesteps (= len(metadata.times))
    "variables": [
      // One entry per binary array, in the order they appear in the payload.
      // `dtype` is one of "uint8" | "int8" | "int16" (see DTYPE_INFO below);
      // `sentinel` is the encoded value that means "null" (no data — e.g. land
      // cells for wave fields). `transform` is "linear" (default) or "sqrt";
      // the decoder applies the inverse (identity / square). Real value is:
      //     real = inv_transform(int_value / scale)   (when int_value != sentinel)
      { "name":      "wave_height",
        "dtype":     "uint8",
        "scale":     90.51,          // step depends on transform; for sqrt,
        "sentinel":  255,
        "transform": "sqrt" },       // encode sqrt(real); decode squares the result
      { "name": "wave_dir",    "dtype": "uint8", "scale": 0.5, "sentinel": 255,  "transform": "linear" },
      { "name": "wave_period", "dtype": "uint8", "scale": 8,   "sentinel": 255,  "transform": "linear" },
      { "name": "water_level", "dtype": "int8",  "scale": 40,  "sentinel": -128, "transform": "linear" }
    ]
  }

The reader is `decodeBinary` in `js/app.js`.

Quick start
-----------
  python grib2bin.py mtr_nwps_CG3_20260430_1200.grib2 --list
  python grib2bin.py mtr_nwps_CG3_20260430_1200.grib2

"""

import argparse
import gzip
import json
import re
import struct
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pygrib

# ── Variable shortNames ───────────────────────────────────────────────────────

HEIGHT_NAME = "swh"
DIR_NAME = "dirpw"
PERIOD_NAME = "perpw"
LEVEL_NAME = "zos"

# ── Binary format ────────────────────────────────────────────────────────────

# Per-dtype encoding range (sentinel is the most-negative or most-positive value).
#   uint8: usable 0..254, sentinel = 255
#   int8:  usable -127..127, sentinel = -128
#   int16: usable -32767..32767, sentinel = -32768
DTYPE_INFO: dict[str, dict[str, Any]] = {
    "uint8": {"np": np.uint8, "lo": 0, "hi": 254, "sentinel": 255, "bytes": 1, "endian": "u1"},
    "int8": {"np": np.int8, "lo": -127, "hi": 127, "sentinel": -128, "bytes": 1, "endian": "i1"},
    "int16": {"np": np.int16, "lo": -32767, "hi": 32767, "sentinel": -32768, "bytes": 2, "endian": "<i2"},
}

# Per-variable quantization. Encoding is:
#     int_value = round(transform(real) * scale)
# and decoding inverts it:
#     real = inverse_transform(int_value / scale)
# `transform` defaults to "linear" (identity). "sqrt" applies sqrt on encode and
# square on decode — gives more resolution at small values, less at large ones,
# useful for non-negative fields whose distribution skews small (e.g. wave height).
#   wave_height: sqrt-encoded, 0–8 m
#   wave_dir   : linear, 0–360 °  step 1/0.5 °  (2 °)
#   wave_period: linear, 0–32 s   step 1/8 s    (0.125 s)
#   water_level: linear, ±3.2 m   step 1/40 m   (2.5 cm)
QUANT: dict[str, dict[str, Any]] = {
    "wave_height": {"dtype": "uint8", "scale": 256 / np.sqrt(8), "transform": "sqrt"},
    "wave_dir": {"dtype": "uint8", "scale": 0.5},
    "wave_period": {"dtype": "uint8", "scale": 8},
    "water_level": {"dtype": "int8", "scale": 40},
}

# ── Types ─────────────────────────────────────────────────────────────────────

Msg = dict[str, Any]

# ── Helpers ──────────────────────────────────────────────────────────────────


def list_variables(path: Path) -> None:
    grbs = pygrib.open(path)
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

    print(f"\nFound {len(seen)} unique variables in {path.name}:\n")
    for (short, name, ltype, level), info in seen.items():
        print(
            f"  shortName={short!r:15s}  steps={info['count']:3d}  "
            f"shape={info['shape']}  typeOfLevel={ltype}  level={level}"
        )
        print(f"    name={name!r}")
    print()


def load_all_messages(path: Path) -> tuple[list[Msg] | None, ...]:
    """Single-pass read; returns msgs for (height, dir, period, level), or None if not present."""
    targets: dict[str, list[Msg]] = {
        HEIGHT_NAME.lower(): [],
        DIR_NAME.lower(): [],
        PERIOD_NAME.lower(): [],
        LEVEL_NAME.lower(): [],
    }

    grbs = pygrib.open(path)
    for grb in grbs:
        sn = grb.shortName.lower()
        if sn not in targets:
            continue

        vals = grb.values
        if hasattr(vals, "filled"):
            vals = vals.filled(np.nan)
        lats, lons = grb.latlons()
        targets[sn].append(
            {
                "shortName": grb.shortName,
                "validDate": grb.validDate,
                "analDate": getattr(grb, "analDate", None),
                "values": vals,
                "lats": lats,
                "lons": lons,
            }
        )
    grbs.close()

    def sort_or_none(msgs: list[Msg]) -> list[Msg] | None:
        return sorted(msgs, key=lambda m: m["validDate"]) if msgs else None

    return tuple(sort_or_none(targets[k.lower()]) for k in (HEIGHT_NAME, DIR_NAME, PERIOD_NAME, LEVEL_NAME))


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


def quantize(
    arr: npt.NDArray[Any],
    dtype: str,
    scale: float,
    transform: str = "linear",
) -> npt.NDArray[Any]:
    """[nt,ny,nx] → typed [ny*nx, nt]; NaN/Inf → sentinel, out-of-range clipped."""
    info = DTYPE_INFO[dtype]
    nt, ny, nx = arr.shape
    # Cell-major, time-minor: result[y*nx + x, t] = arr[t, y, x]
    flat = np.transpose(arr, (1, 2, 0)).reshape(ny * nx, nt)
    nan_mask = ~np.isfinite(flat)
    val = np.where(nan_mask, 0.0, flat)
    if transform == "linear":
        pass
    elif transform == "sqrt":
        # Inverse is `square` on the JS side; negative inputs shouldn't occur
        # for sqrt-encoded fields, but clamp to 0 to avoid NaN from sqrt(<0).
        val = np.sqrt(np.maximum(val, 0.0))
    else:
        raise ValueError(f"unknown transform: {transform!r}")
    q = np.round(val * scale)
    np.clip(q, info["lo"], info["hi"], out=q)
    q[nan_mask] = info["sentinel"]
    return q.astype(info["np"])


def write_binary(
    out_path: Path,
    metadata: dict[str, Any],
    arrays: list[tuple[str, npt.NDArray[Any]]],
) -> None:
    """Write [u32 header_len][JSON header][quantized arrays], gzip-compressed."""
    ncells, nt = arrays[0][1].shape
    for name, arr in arrays:
        assert arr.shape == (ncells, nt), f"{name} shape mismatch: {arr.shape}"

    header = {
        "version": 1,
        "metadata": metadata,
        "ncells": int(ncells),
        "nt": int(nt),
        "variables": [
            {
                "name": name,
                "dtype": QUANT[name]["dtype"],
                "scale": QUANT[name]["scale"],
                "sentinel": DTYPE_INFO[QUANT[name]["dtype"]]["sentinel"],
                "transform": QUANT[name].get("transform", "linear"),
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
        for name, arr in arrays:
            endian = DTYPE_INFO[QUANT[name]["dtype"]]["endian"]
            f.write(arr.astype(endian, copy=False).tobytes())


# ── Main ─────────────────────────────────────────────────────────────────────


def _out_filename(input_path: Path) -> str:
    """Derive `waves_<run_id>.bin.gz` from an `mtr_nwps_CG3_<run_id>.grib2` name."""
    m = re.search(r"(\d{8}_\d{4})", input_path.name)
    if not m:
        raise ValueError(f"could not parse run id (YYYYMMDD_HHMM) from {input_path.name!r}; pass --out explicitly")
    return f"waves_{m.group(1)}.bin.gz"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert NWPS GRIB2 → waves.bin.gz for the dive conditions viewer.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # fmt: off
    ap.add_argument(
        "input", type=Path, help="Input .grib2 file",
    )
    ap.add_argument(
        "--list", "-l", action="store_true", help="List available variables and exit",
    )
    ap.add_argument(
        "--out-dir", "-o", type=Path, default=Path("./data/"), help="Output directory to save .bin.gz file",
    )
    # fmt: on
    args = ap.parse_args()

    path: Path = args.input
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if args.list:
        list_variables(path)
        return

    print("Reading GRIB2 messages...")
    msgs_h, msgs_d, msgs_p, msgs_l = load_all_messages(path)

    if msgs_h is None:
        raise ValueError(f"Wave height variable '{HEIGHT_NAME}' not found. Run --list to see available variables.")

    def report(label: str, msgs: list[Msg] | None) -> None:
        if msgs:
            print(f"  {label:11s}  → {msgs[0]['shortName']} ({len(msgs)} steps)")
        else:
            print(f"  {label:11s}  → NOT FOUND (will be null)")

    report("wave_height", msgs_h)
    report("wave_dir", msgs_d)
    report("wave_period", msgs_p)
    report("water_level", msgs_l)

    times_iso, lats, lons, arr_h, ref_time = extract(msgs_h)
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
        "units": {"wave_height": "m", "wave_dir": "deg", "wave_period": "s", "water_level": "m"},
    }

    def empty(name: str) -> npt.NDArray[Any]:
        info = DTYPE_INFO[QUANT[name]["dtype"]]
        return np.full((ny * nx, nt), info["sentinel"], dtype=info["np"])

    arrays: list[tuple[str, npt.NDArray[Any]]] = []
    arrays.append(("wave_height", quantize(arr_h, **QUANT["wave_height"])))

    if msgs_d:
        _, _, _, arr_d, _ = extract(msgs_d)
        arrays.append(("wave_dir", quantize(arr_d, **QUANT["wave_dir"])))
    else:
        arrays.append(("wave_dir", empty("wave_dir")))

    if msgs_p:
        _, _, _, arr_p, _ = extract(msgs_p)
        arrays.append(("wave_period", quantize(arr_p, **QUANT["wave_period"])))
    else:
        arrays.append(("wave_period", empty("wave_period")))

    if msgs_l:
        _, _, _, arr_l, _ = extract(msgs_l)
        arrays.append(("water_level", quantize(arr_l, **QUANT["water_level"])))
    else:
        arrays.append(("water_level", empty("water_level")))

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _out_filename(path)
    write_binary(out_path, metadata, arrays)

    size_kb = out_path.stat().st_size / 1e3
    print(f"\nWrote {out_path}  ({size_kb:.1f} kB)")


if __name__ == "__main__":
    main()
