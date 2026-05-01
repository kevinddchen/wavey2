# Monterey Bay Dive Conditions

A static webpage that visualizes scuba diving conditions (wave height, wave direction, water level) for the Monterey Bay area from NOAA NWPS GRIB2 forecast data.

## Features

- Interactive Leaflet map with a wave-height heatmap overlay (blue = calm, red = rough)
- Time slider and play button to animate through forecast time steps
- Click any ocean point to display time-series charts for that location:
  - Wave height (m)
  - Wave direction (0–360°, with compass labels)
  - Water level / tide (m)
- A yellow cursor on the charts tracks the currently selected time step

## Project structure

```
wavey2/
├── index.html            # Main page
├── css/style.css         # Dark marine theme
├── js/app.js             # Map, heatmap, and chart logic
├── data/waves.json       # Pre-processed forecast data (generated from GRIB2)
└── scripts/grib2json.py  # GRIB2 → JSON conversion script
```

## Data format

`data/waves.json` contains a regular lat/lon grid with time-series arrays at each grid point:

```json
{
  "metadata": {
    "source": "NOAA NWPS – mtr_nwps_CG3_20260430_1200.grib2",
    "forecast_time": "2026-04-30T12:00:00Z",
    "times": ["2026-04-30T13:00:00Z", "..."],
    "grid": { "nx": 90, "ny": 178, "lat_min": 36.2, "lat_max": 37.0, "lon_min": -122.2, "lon_max": -121.7 },
    "units": { "wave_height": "m", "wave_dir": "°", "water_level": "m" }
  },
  "wave_height":  [[t0, t1, ...], ...],  // [ny×nx grid points][time steps]
  "wave_dir":     [[t0, t1, ...], ...],
  "water_level":  [[t0, t1, ...], ...]   // null if not available in source
}
```

Grid point index: `i = y * nx + x`, where `y = 0` is the southernmost row.

## Generating data from a new GRIB2 file

### Requirements

```bash
uv venv
uv pip install pygrib numpy
```

`pygrib` requires the `eccodes` C library. On macOS install it via Homebrew if not already present:

```bash
brew install eccodes
```

### List available variables

```bash
uv run python scripts/grib2json.py YOUR_FILE.grib2 --list
```

### Convert

```bash
uv run python scripts/grib2json.py YOUR_FILE.grib2
```

## Local development

```bash
python3 -m http.server 8042 --directory /path/to/wavey2
open http://localhost:8042
```

A plain file server is required because the page uses `fetch()` to load `data/waves.json`, which browsers block over `file://` URLs.

## Hosting on GitHub Pages

1. Push this repository to GitHub.
2. Go to **Settings → Pages** and set the source to the `main` branch, root directory.
3. The site will be available at `https://<username>.github.io/<repo>/`.

No build step is needed — everything is static HTML, CSS, and JavaScript.
