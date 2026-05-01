"""
Convert an NWPS GRIB2 file to waves.json for the dive-conditions viewer.

Quick start
-----------
  # 1. List what variables are in the file
  python3 grib2json.py mtr_nwps_CG3_20260430_1200.grib2 --list

  # 2. Convert using auto-detected variable names
  python3 grib2json.py mtr_nwps_CG3_20260430_1200.grib2

  # 3. Override variable names if auto-detection fails
  python3 grib2json.py mtr_nwps_CG3_20260430_1200.grib2 \
      --wave-height swh --wave-dir mwd --water-level ssh

Requirements
------------
  pip install pygrib numpy
  brew install eccodes          # macOS
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

# ── Variable shortName candidates (tried in order) ───────────────────────────

HEIGHT_NAMES = ['swh', 'htsgw', 'shww', 'wvhgt']
DIR_NAMES    = ['mwd', 'dirpw', 'mdts']
LEVEL_NAMES  = ['ssh', 'zos', 'wlev', 'surge']

# ── Helpers ──────────────────────────────────────────────────────────────────

def open_file(path):
    try:
        import pygrib
    except ImportError:
        sys.exit("ERROR: Run: pip install pygrib")
    return pygrib.open(str(path))


def list_variables(path):
    grbs = open_file(path)
    seen = {}
    for grb in grbs:
        key = (grb.shortName, grb.name, grb.typeOfLevel, grb.level)
        if key not in seen:
            try:
                shape = (grb.Nj, grb.Ni)
            except AttributeError:
                shape = (grb.numberOfDataPoints,)
            seen[key] = {'count': 0, 'shape': shape}
        seen[key]['count'] += 1
    grbs.close()

    print(f"\nFound {len(seen)} unique variable(s) in {path.name}:\n")
    for (short, name, ltype, level), info in seen.items():
        print(f"  shortName={short!r:15s}  steps={info['count']:3d}  "
              f"shape={info['shape']}  typeOfLevel={ltype}  level={level}")
        print(f"    name={name!r}")
    print()


def _best_group(groups):
    """From a dict of shortName→[msgs], return the list with the most entries."""
    if not groups:
        return None, None
    sn = max(groups, key=lambda k: len(groups[k]))
    return sorted(groups[sn], key=lambda m: m['validDate']), sn


def load_all_messages(path, h_cands, d_cands, l_cands):
    """Single-pass read; returns (msgs_h, sn_h), (msgs_d, sn_d), (msgs_l, sn_l)."""
    h_low = {c.lower() for c in h_cands}
    d_low = {c.lower() for c in d_cands}
    l_low = {c.lower() for c in l_cands}

    h_groups, d_groups, l_groups = {}, {}, {}

    grbs = open_file(path)
    for grb in grbs:
        sn = grb.shortName.lower()
        if sn in h_low:
            target = h_groups
        elif sn in d_low:
            target = d_groups
        elif sn in l_low:
            target = l_groups
        else:
            continue

        vals = grb.values
        if hasattr(vals, 'filled'):
            vals = vals.filled(np.nan)
        lats, lons = grb.latlons()
        entry = {
            'shortName': grb.shortName,
            'validDate': grb.validDate,
            'analDate': getattr(grb, 'analDate', None),
            'values': vals,
            'lats': lats,
            'lons': lons,
        }
        target.setdefault(sn, []).append(entry)
    grbs.close()

    return _best_group(h_groups), _best_group(d_groups), _best_group(l_groups)


def extract(msgs):
    """Return (times_iso, lat_1d, lon_1d, values[nt,ny,nx], ref_time_iso)."""
    lats2d = msgs[0]['lats']
    lons2d = msgs[0]['lons']

    data = np.stack([m['values'] for m in msgs], axis=0)  # [nt, ny, nx]

    times_iso = [m['validDate'].strftime('%Y-%m-%dT%H:%M:%SZ') for m in msgs]
    ref = msgs[0]['analDate']
    ref_time_iso = ref.strftime('%Y-%m-%dT%H:%M:%SZ') if ref else None

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


def to_grid_list(arr, ndecimals):
    """[nt,ny,nx] → list[ny*nx] of list[nt] values; NaN/Inf → null."""
    nt, ny, nx = arr.shape
    out = []
    for y in range(ny):
        for x in range(nx):
            row = []
            for v in arr[:, y, x]:
                if np.isnan(v) or np.isinf(v):
                    row.append(None)
                else:
                    row.append(round(float(v), ndecimals))
            out.append(row)
    return out

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description='Convert NWPS GRIB2 → waves.json for the dive conditions viewer.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('input', help='Input .grib2 file')
    ap.add_argument('--list', action='store_true', help='List available variables and exit')
    ap.add_argument('--out', default='data/waves.json',
                    help='Output path (default: data/waves.json)')
    ap.add_argument('--wave-height', metavar='VAR',
                    help='GRIB2 shortName for wave height')
    ap.add_argument('--wave-dir', metavar='VAR',
                    help='GRIB2 shortName for wave direction')
    ap.add_argument('--water-level', metavar='VAR',
                    help='GRIB2 shortName for water level / tide')
    ap.add_argument('--round', type=int, default=2, metavar='N',
                    help='Decimal places to round values to (default: 2)')
    ap.add_argument('--step', type=int, default=1, metavar='N',
                    help='Keep every Nth time step, e.g. --step 3 for 3-hourly (default: 1)')
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        sys.exit(f"ERROR: File not found: {path}")

    if args.list:
        list_variables(path)
        return

    h_cands = [args.wave_height] if args.wave_height else HEIGHT_NAMES
    d_cands = [args.wave_dir]    if args.wave_dir    else DIR_NAMES
    l_cands = [args.water_level] if args.water_level else LEVEL_NAMES

    print("Reading GRIB2 messages...")
    (msgs_h, sn_h), (msgs_d, sn_d), (msgs_l, sn_l) = load_all_messages(
        path, h_cands, d_cands, l_cands
    )

    if msgs_h is None:
        print("ERROR: Wave height variable not found. Run --list to see available variables.")
        print(f"  Tried shortNames: {h_cands}")
        sys.exit(1)

    print(f"  wave_height  → {sn_h} ({len(msgs_h)} steps)")
    print(f"  wave_dir     → {sn_d + ' (' + str(len(msgs_d)) + ' steps)' if sn_d else 'NOT FOUND (will be null)'}")
    print(f"  water_level  → {sn_l + ' (' + str(len(msgs_l)) + ' steps)' if sn_l else 'NOT FOUND (will be null)'}")

    times_iso, lats, lons, arr_h, ref_time = extract(msgs_h)
    if args.step > 1:
        arr_h     = arr_h[::args.step]
        times_iso = times_iso[::args.step]
    nt, ny, nx = arr_h.shape

    print(f"\n  Grid: {nx}×{ny},  {nt} time steps")
    print(f"  Lat:  {lats[0]:.4f} → {lats[-1]:.4f}")
    print(f"  Lon:  {lons[0]:.4f} → {lons[-1]:.4f}")

    grid = {
        'nx': int(nx), 'ny': int(ny),
        'lat_min': float(lats[0]),  'lat_max': float(lats[-1]),
        'lon_min': float(lons[0]),  'lon_max': float(lons[-1]),
    }

    out = {
        'metadata': {
            'source': f'NOAA NWPS – {path.name}',
            'forecast_time': ref_time or times_iso[0],
            'times': times_iso,
            'grid': grid,
            'units': {'wave_height': 'm', 'wave_dir': '°', 'water_level': 'm'},
        },
        'wave_height': to_grid_list(arr_h, args.round),
    }

    if msgs_d:
        _, _, _, arr_d, _ = extract(msgs_d)
        out['wave_dir'] = to_grid_list(arr_d[::args.step], 0)
    else:
        out['wave_dir'] = [[None] * nt] * (ny * nx)

    if msgs_l:
        _, _, _, arr_l, _ = extract(msgs_l)
        out['water_level'] = to_grid_list(arr_l[::args.step], args.round)
    else:
        out['water_level'] = None

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(out, f, separators=(',', ':'))

    size_mb = out_path.stat().st_size / 1e6
    print(f"\nWrote {out_path}  ({size_mb:.1f} MB)")
    if size_mb > 20:
        print("  Tip: large file — consider downsampling the grid before converting.")


if __name__ == '__main__':
    main()
