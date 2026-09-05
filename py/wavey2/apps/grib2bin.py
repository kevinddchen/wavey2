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
The `wavey2.header.Header` model, serialized compactly. It declares the grid, the
time axis, and the per-variable quantization (`dtype`, `scale`, `sentinel`,
`transform`) that the decoder needs to turn the payload back into real values.
`build_index` validates the same model when it reads the header back.

The reader is `decodeBinary` in `js/app.js`.

Quick start
-----------
  python -m wavey2.apps.grib2bin mtr_nwps_CG3_20260430_1200.grib2 --list-only
  python -m wavey2.apps.grib2bin mtr_nwps_CG3_20260430_1200.grib2

"""

import gzip
import logging
import re
import struct
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import numpy.typing as npt
import pygrib
import tyro

from wavey2.header import Grid, Header, Metadata, Units, Variable
from wavey2.logging import setup_logging

LOG = logging.getLogger(Path(__file__).stem)

# ── Variable shortNames ───────────────────────────────────────────────────────

_HEIGHT_NAME = "swh"
_DIR_NAME = "dirpw"
_PERIOD_NAME = "perpw"
_LEVEL_NAME = "zos"

# ── Binary format ────────────────────────────────────────────────────────────

# Per-dtype encoding range (sentinel is the most-negative or most-positive value).
#   uint8: usable 0..254, sentinel = 255
#   int8:  usable -127..127, sentinel = -128
#   int16: usable -32767..32767, sentinel = -32768
_DTYPE_INFO: dict[str, dict[str, Any]] = {
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
_QUANT: dict[str, dict[str, Any]] = {
    "wave_height": {"dtype": "uint8", "scale": 256 / np.sqrt(8), "transform": "sqrt"},
    "wave_dir": {"dtype": "uint8", "scale": 0.5},
    "wave_period": {"dtype": "uint8", "scale": 8},
    "water_level": {"dtype": "int8", "scale": 40},
}

# ── Types ─────────────────────────────────────────────────────────────────────

Msg = dict[str, Any]


class Loaded(NamedTuple):
    """Target messages plus the lat/lon grid they all share."""

    lats: npt.NDArray[Any] | None
    lons: npt.NDArray[Any] | None
    height: list[Msg] | None
    dir: list[Msg] | None
    period: list[Msg] | None
    level: list[Msg] | None


# ── Helpers ──────────────────────────────────────────────────────────────────


def list_variables(path: Path) -> None:
    """Log every distinct variable in a GRIB2 file, for picking the `*_NAME` constants above."""
    grbs = pygrib.open(path)  # ty: ignore[unresolved-attribute]
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

    # One record, so the table isn't broken up by a log prefix per line.
    lines = [f"Found {len(seen)} unique variable(s) in {path.name}:"]
    for (short, name, ltype, level), info in seen.items():
        lines.append(
            f"  shortName={short!r:15s}  steps={info['count']:3d}  "
            f"shape={info['shape']}  typeOfLevel={ltype}  level={level}"
        )
        lines.append(f"    name={name!r}")
    LOG.info("\n".join(lines))


def load_all_messages(path: Path) -> Loaded:
    """Single-pass read; per-variable msgs are None if the variable isn't present."""
    targets: dict[str, list[Msg]] = {
        _HEIGHT_NAME: [],
        _DIR_NAME: [],
        _PERIOD_NAME: [],
        _LEVEL_NAME: [],
    }
    # Every message is on the same grid, and `latlons()` is expensive (it rebuilds
    # the projection and meshgrids), so read it from the first match only.
    lats: npt.NDArray[Any] | None = None
    lons: npt.NDArray[Any] | None = None

    grbs = pygrib.open(path)  # ty: ignore[unresolved-attribute]
    for grb in grbs:
        sn = grb.shortName
        if sn not in targets:
            continue

        vals = grb.values
        if hasattr(vals, "filled"):
            vals = vals.filled(np.nan)
        if lats is None:
            lats, lons = grb.latlons()
        targets[sn].append(
            {
                "shortName": grb.shortName,
                "validDate": grb.validDate,
                "analDate": getattr(grb, "analDate", None),
                "values": vals,
            }
        )
    grbs.close()

    def sort_or_none(msgs: list[Msg]) -> list[Msg] | None:
        return sorted(msgs, key=lambda m: m["validDate"]) if msgs else None

    return Loaded(
        lats,
        lons,
        sort_or_none(targets[_HEIGHT_NAME]),
        sort_or_none(targets[_DIR_NAME]),
        sort_or_none(targets[_PERIOD_NAME]),
        sort_or_none(targets[_LEVEL_NAME]),
    )


def extract(
    msgs: list[Msg],
    lats2d: npt.NDArray[Any],
    lons2d: npt.NDArray[Any],
) -> tuple[list[str], npt.NDArray[Any], npt.NDArray[Any], npt.NDArray[Any], str | None]:
    """Return (times_iso, lat_1d, lon_1d, values[nt,ny,nx], ref_time_iso)."""
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
    info = _DTYPE_INFO[dtype]
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
    metadata: Metadata,
    arrays: list[tuple[str, npt.NDArray[Any]]],
) -> None:
    """Write [u32 header_len][JSON header][quantized arrays], gzip-compressed."""
    ncells, nt = arrays[0][1].shape
    for name, arr in arrays:
        assert arr.shape == (ncells, nt), f"{name} shape mismatch: {arr.shape}"

    header = Header(
        metadata=metadata,
        ncells=int(ncells),
        nt=int(nt),
        variables=[
            Variable(
                name=name,
                dtype=_QUANT[name]["dtype"],
                scale=_QUANT[name]["scale"],
                sentinel=_DTYPE_INFO[_QUANT[name]["dtype"]]["sentinel"],
                transform=_QUANT[name].get("transform", "linear"),
            )
            for name, _ in arrays
        ],
    )

    header_bytes = header.model_dump_json().encode("utf-8")
    # Pad so the binary payload starts on a 4-byte boundary
    pad = (-len(header_bytes)) % 4
    header_bytes += b" " * pad

    with gzip.open(out_path, "wb", compresslevel=9) as f:
        f.write(struct.pack("<I", len(header_bytes)))
        f.write(header_bytes)
        for name, arr in arrays:
            endian = _DTYPE_INFO[_QUANT[name]["dtype"]]["endian"]
            f.write(arr.astype(endian, copy=False).tobytes())


# ── Main ─────────────────────────────────────────────────────────────────────


def _out_filename(input_path: Path) -> str:
    """Derive `waves_<run_id>.bin.gz` from an `mtr_nwps_CG3_<run_id>.grib2` name."""
    m = re.search(r"(\d{8}_\d{4})", input_path.name)
    if not m:
        raise ValueError(
            f"could not parse run id (YYYYMMDD_HHMM) from {input_path.name!r}; "
            "expected a name like 'mtr_nwps_CG3_20260430_1200.grib2'"
        )
    return f"waves_{m.group(1)}.bin.gz"


def main(
    path: Path,
    /,
    list_only: bool = False,
    out_dir: Path = Path("./data/"),
) -> None:
    """
    Convert NWPS GRIB2 → waves.bin.gz for the dive conditions viewer.

    Args:
        path: Input .grib2 file.
        list_only: List available variables and exit.
        out_dir: Output directory to save .bin.gz file.
    """

    if list_only:
        list_variables(path)
        return

    lats2d, lons2d, msgs_h, msgs_d, msgs_p, msgs_l = load_all_messages(path)

    if msgs_h is None:
        raise ValueError(
            f"Wave height variable '{_HEIGHT_NAME}' not found. Run --list-only to see available variables."
        )
    # Set alongside the first matched message, so non-None whenever `msgs_h` is.
    assert lats2d is not None and lons2d is not None

    times_iso, lats, lons, arr_h, ref_time = extract(msgs_h, lats2d, lons2d)
    nt, ny, nx = arr_h.shape

    grid = Grid(
        nx=int(nx),
        ny=int(ny),
        lat_min=float(lats[0]),
        lat_max=float(lats[-1]),
        lon_min=float(lons[0]),
        lon_max=float(lons[-1]),
    )

    metadata = Metadata(
        source=f"NOAA NWPS – {path.name}",
        forecast_time=ref_time or times_iso[0],
        times=times_iso,
        grid=grid,
        units=Units(wave_height="m", wave_dir="deg", wave_period="s", water_level="m"),
    )

    def empty(name: str) -> npt.NDArray[Any]:
        info = _DTYPE_INFO[_QUANT[name]["dtype"]]
        return np.full((ny * nx, nt), info["sentinel"], dtype=info["np"])

    arrays: list[tuple[str, npt.NDArray[Any]]] = []
    arrays.append(("wave_height", quantize(arr_h, **_QUANT["wave_height"])))

    if msgs_d:
        _, _, _, arr_d, _ = extract(msgs_d, lats2d, lons2d)
        arrays.append(("wave_dir", quantize(arr_d, **_QUANT["wave_dir"])))
    else:
        arrays.append(("wave_dir", empty("wave_dir")))

    if msgs_p:
        _, _, _, arr_p, _ = extract(msgs_p, lats2d, lons2d)
        arrays.append(("wave_period", quantize(arr_p, **_QUANT["wave_period"])))
    else:
        arrays.append(("wave_period", empty("wave_period")))

    if msgs_l:
        _, _, _, arr_l, _ = extract(msgs_l, lats2d, lons2d)
        arrays.append(("water_level", quantize(arr_l, **_QUANT["water_level"])))
    else:
        arrays.append(("water_level", empty("water_level")))

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _out_filename(path)
    write_binary(out_path, metadata, arrays)

    size_kb = out_path.stat().st_size / 1e3
    LOG.info(f"Wrote '{out_path}' ({size_kb:.1f} kB)")


if __name__ == "__main__":
    setup_logging()
    tyro.cli(main)
