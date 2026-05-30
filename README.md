# Monterey Bay Dive Conditions

[_--> live website <--_](https://kevinddchen.github.io/wavey2/)

> **Disclaimer:** This project was vibe-coded with Claude.

A static webpage that visualizes scuba diving conditions (wave height, wave period, wave direction, water level / tide) for the Monterey Bay area from NOAA NWPS GRIB2 forecast data.

This is a rewrite of an [older version](https://github.com/kevinddchen/wavey) of the project.

## Features

- Interactive map with a wave-height heatmap overlay
- Time slider and play button to animate through forecast time steps
- Click any ocean point to display time-series charts for that location:
    - Wave height
    - Wave period
    - Wave direction
    - Water level / tide
- **Compare two dive sites:** right-click any ocean point to drop a gold comparison marker; its data is overlaid on every chart alongside the blue (primary) series
- **Dive-site shortcuts:** two dropdowns at the bottom of the sidebar list common Monterey Bay dive sites — pick one in the blue (primary) dropdown or the gold (comparison) dropdown to jump the corresponding marker there.
- **Keyboard shortcuts:** `Space` toggles play/pause and `←` / `→` step the time slider one frame at a time.
- **Drag-to-scrub charts:** click and drag anywhere on a chart to scrub the time slider to that point.

### URL parameters

The current marker positions are encoded in the URL so you can bookmark or share a link to a specific view:

| Parameter         | Description                                             |
| ----------------- | ------------------------------------------------------- |
| `lat`             | Latitude of the blue (primary) marker                   |
| `lon`             | Longitude of the blue (primary) marker                  |
| `cmpLat`          | Latitude of the gold (comparison) marker                |
| `cmpLon`          | Longitude of the gold (comparison) marker               |
| `zoom`            | Initial map zoom level (integer, 0–18)                  |
| `forecast`        | Id of the forecast run to show (e.g. `20260528_1200`)   |
| `t`               | Initial time-slider index (integer)                     |
| `units`           | Display units — `ft` (default) or `m`                   |
| `charts`          | Comma-separated whitelist from `height,period,dir,tide` |
| `hideMap`         | Hide the map panel                                      |
| `hideSidebar`     | Hide the sidebar                                        |
| `hideHeader`      | Hide the sidebar header / title block                   |
| `hideSelectors`   | Hide the forecast + dive-site selector row              |
| `hideTimeControl` | Hide the time slider / play button / legend bar         |
| `hideFooter`      | Hide the "View on GitHub" footer link                   |

## Embedding on another site

The page can be embedded via `<iframe>`. A minimal example:

```html
<iframe src="https://kevinddchen.github.io/wavey2/" loading="lazy"></iframe>
```

Use the URL parameters above to deep-link to a specific location. For example, to open with the marker on Lovers Point:

```html
<iframe src="https://kevinddchen.github.io/wavey2/?lat=36.6249&lon=-121.9135" loading="lazy"></iframe>
```

## NOAA Nearshore Wave Prediction System

The [NOAA Nearshore Wave Prediction System (NWPS)](https://polar.ncep.noaa.gov/nwps/) is a NOAA service that produces
wave forecasts for U.S. coastal areas. This project pulls forecast data for Monterey Bay and visualizes it as a heatmap.
A live NOAA visualization of wave heights for Monterey Bay is available
[here](https://polar.ncep.noaa.gov/nwps/nwpsloop.php?site=MTR&loop=sigwaveheight&cg=3).

## Generating data from GRIB2 file

### Requirements

```bash
uv venv
uv sync --no-dev
```

### Download GRIB2 files

```bash
# the single most recent run:
uv run scripts/download_grib.py --out-dir gribs/
# or every run still on the server:
uv run scripts/download_grib.py --all --out-dir gribs/
```

### Convert data to binary

```bash
uv run scripts/grib2bin.py YOUR_FILE.grib2 --out-dir data/
```

This creates one file per run named `data/waves_<run_id>.bin.gz` (the `<run_id>`,
e.g. `20260528_1200`, is parsed from the input filename). After converting all the
runs you want available, build the manifest the website reads:

```bash
uv run scripts/build_index.py --dir data/  # writes data/index.json
```

`data/index.json` is a newest-first list of `{ id, file, forecast_time, source }`;
the page fetches it to populate the forecast selector and pick which run to show
(overridable with the `forecast` URL param). Each `waves_<run_id>.bin.gz` has the
uncompressed payload layout:

```
[4 bytes : LE u32]    header byte length (includes padding)
[N bytes : UTF-8]     JSON header (padded so the payload is 4-byte aligned)
[binary  : LE typed]  one ncells×nt array per variable, in `header.variables` order
                      (cell-major, time-minor); dtype/sentinel are declared per
                      variable in the header (currently uint8/int8, sentinel = the
                      most-extreme representable value)
```

The whole file is gzipped on disk so the wire transfer stays small.
The JSON header contains the metadata (source, forecast_time, times, grid, units) plus per-variable `scale`/`sentinel`/`transform` for dequantization: `real_value = inverse_transform(int_value / scale)`, where `transform` is `"linear"` (identity) or `"sqrt"` (decoder squares the result). `sentinel` is the encoded value meaning "no data".
See `scripts/grib2bin.py` for the writer and `js/app.js` (`decodeBinary`) for the reader.

Grid point index: `i = y * nx + x`, where `y = 0` is the southernmost row.

## Local development

```bash
uv run python -m http.server 8000 & open http://localhost:8000
```

A plain file server is required because the page uses `fetch()` to load `data/index.json` and the `data/waves_<run_id>.bin.gz` files, which browsers block over `file://` URLs.

## Hosting on GitHub Pages

1. Push this repository to GitHub.
2. Go to **Settings → Pages** and set the source to **GitHub Actions**.
3. The site will be available at `https://<username>.github.io/<repo>/`.

The `.github/workflows/deploy.yml` workflow handles the build and deploy. It runs automatically four times a day (02:00, 08:00, 14:00, and 20:00 UTC) to refresh the forecast data, and can also be triggered manually from the Actions tab.

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
uv run ruff format --check
```

### Javascript checks

```bash
npm run eslint
npm run prettier
```
