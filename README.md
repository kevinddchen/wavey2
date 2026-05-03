# Monterey Bay Dive Conditions

[_--> live website <--_](https://kevinddchen.github.io/wavey2/)

> **Disclaimer:** This project was vibe-coded with Claude.

A static webpage that visualizes scuba diving conditions (wave height, wave period, wave direction, water level) for the Monterey Bay area from NOAA NWPS GRIB2 forecast data.

This is a rewrite of an [older version](https://github.com/kevinddchen/wavey) of the project.

## Features

- Interactive Leaflet map with a wave-height heatmap overlay (blue = calm, red = rough)
- Time slider and play button to animate through forecast time steps
- Click any ocean point to display time-series charts for that location:
    - Significant wave height (ft)
    - Wave period (sec)
    - Wave direction (deg)
    - Water level / tide (ft)
- A yellow cursor on the charts tracks the currently selected time step

## NOAA Nearshore Wave Prediction System

The [NOAA Nearshore Wave Prediction System (NWPS)](https://polar.ncep.noaa.gov/nwps/) is a NOAA service that produces
wave forecasts for U.S. coastal areas. This project pulls forecast data for Monterey Bay and visualizes it as a heatmap.
A live NOAA visualization of wave heights for Monterey Bay is available
[here](https://polar.ncep.noaa.gov/nwps/nwpsloop.php?site=MTR&loop=sigwaveheight&cg=3).

## Project structure

```
wavey2/
├── index.html                          # Main page
├── css/style.css                       # Dark marine theme
├── js/app.js                           # Map, heatmap, and chart logic
├── data/waves.json                     # Pre-processed forecast data (generated from GRIB2)
├── scripts/
│   ├── download_latest_grib.py         # Download latest GRIB2 file from NOAA
│   └── grib2json.py                    # GRIB2 → JSON conversion script
├── .github/workflows/
│   ├── check.yml                       # Python and JS lint/format checks
│   └── deploy.yml                      # Deploy to GitHub Pages
├── pyproject.toml                      # Python project config (uv, mypy, ruff)
└── package.json                        # Node.js config (prettier)
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
    "units": { "wave_height": "ft", "wave_dir": "deg", "wave_period": "sec", "water_level": "ft" }
  },
  "wave_height":  [[t0, t1, ...], ...],  // [ny×nx grid points][time steps]
  "wave_dir":     [[t0, t1, ...], ...],
  "wave_period":  [[t0, t1, ...], ...],
  "water_level":  [[t0, t1, ...], ...]
}
```

Grid point index: `i = y * nx + x`, where `y = 0` is the southernmost row.

## Generating data from GRIB2 file

### Requirements

```bash
uv venv
uv sync --no-dev
```

### Download latest GRIB2 file

```bash
uv run python scripts/download_latest_grib.py
```

### Convert data to JSON

```bash
uv run python scripts/grib2json.py YOUR_FILE.grib2
```

## Local development

```bash
uv run python -m http.server 8000 & open http://localhost:8000
```

A plain file server is required because the page uses `fetch()` to load `data/waves.json`, which browsers block over `file://` URLs.

## Hosting on GitHub Pages

1. Push this repository to GitHub.
2. Go to **Settings → Pages** and set the source to **GitHub Actions**.
3. The site will be available at `https://<username>.github.io/<repo>/`.

The `.github/workflows/deploy.yml` workflow handles the build and deploy. It runs automatically twice a day (07:00 and 13:00 UTC) to refresh the forecast data, and can also be triggered manually from the Actions tab.

## Developer tools

### Requirements

```bash
uv venv
uv sync
npm install
```

### Python checks

```bash
uv run mypy .
uv run ruff check
uv run ruff format
```

### Javascript checks

```bash
npm run prettier
```
