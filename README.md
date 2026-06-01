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
- **Switch forecast runs:** a dropdown in the selector row lists the available forecast runs — pick an earlier run to view a previous forecast. Runs swap in place without reloading the page, and the other runs are prefetched in the background so switching is near-instant.
- **Compare two dive sites:** right-click any ocean point to drop a gold comparison marker; its data is overlaid on every chart alongside the blue (primary) series
- **Dive-site shortcuts:** two dropdowns at the bottom of the sidebar list common Monterey Bay dive sites — pick one in the blue (primary) dropdown or the gold (comparison) dropdown to jump the corresponding marker there.
- **Buoy measurements:** the same dropdowns also list nearby NDBC buoys (e.g. "Buoy 46236 Measurements") — pick one to overlay the buoy's _actual_ observed wave height, period, and direction (last 5 days) on the charts. Because the forecast-run dropdown reaches back ~5 days, selecting an older run lets you compare that forecast against what the buoy actually measured.
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
| `play`            | Autostart the time animation on load                    |
| `units`           | Display units — `ft` (default) or `m`                   |
| `charts`          | Comma-separated whitelist from `height,period,dir,tide` |
| `hideMap`         | Hide the map panel                                      |
| `hideSidebar`     | Hide the sidebar                                        |
| `hideHeader`      | Hide the sidebar header / title block                   |
| `hideSelectors`   | Hide the forecast + dive-site selector row              |
| `hideTimeControl` | Hide the time slider / play button / legend bar         |
| `hideFooter`      | Hide the "View on GitHub" footer link                   |
| `disablePrefetch` | Don't background-download other runs (saves bandwidth)  |

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
uv run scripts/download_grib.py
# or every run still on the server:
uv run scripts/download_grib.py -a
```

### Convert data to binary

```bash
uv run scripts/grib2bin.py gribs/YOUR_FILE.grib2
```

This creates one file per run named `data/waves_<run_id>.bin.gz` (the `<run_id>`,
e.g. `20260528_1200`, is parsed from the input filename). Once you've generated all
the wave files (and any buoy files, below), build the manifest the website reads
with `scripts/build_index.py` (see [Build the manifest](#build-the-manifest)).

`data/index.json` is a `{ forecasts, buoys }` object: `forecasts` is a newest-first
list of `{ id, file, forecast_time, source }` (the page populates the run selector
from it and picks which run to show, overridable with the `forecast` URL param) and
`buoys` is a list of `{ id, file }` (see below). Each `waves_<run_id>.bin.gz` has the
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

### Download buoy observations

In addition to the forecast, the page can overlay **real** measurements from an
[NDBC buoy](https://www.ndbc.noaa.gov/) (e.g. buoy 46236 in Monterey Bay) so a
forecast run can be compared against what the ocean actually did:

```bash
uv run scripts/download_buoy.py -b 46236  # writes data/buoy_46236_<YYYYMMDD_HHMM>.json
```

This fetches the buoy's [realtime2](https://www.ndbc.noaa.gov/data/realtime2/) text
table, keeps the last 5 days of wave observations (thinned to one per hour, since the
charts only resolve hourly), and writes:

```json
{ "name": "Buoy 46236 Measurements",
  "units": { "wave_height": "m", "wave_period": "s", "wave_dir": "deg" },
  "times": ["...Z", ...], "wave_height": [1.4, ...], "wave_period": [13, ...], "wave_dir": [286, ...] }
```

The filename is stamped with the latest observation's hour
(`buoy_46236_20260528_1200.json`) so a refreshed file gets a new URL and can't be
served stale from a browser/CDN cache; the current filename is recorded in
`index.json` (the page reads it from there rather than hardcoding it).

Values are stored raw — `wave_height` in meters and `wave_dir` as the observed
"direction-from" — so the page applies the same unit scaling and +180° "direction-toward"
convention it uses for the forecast (see `initBuoy` in `js/app.js`). Missing readings
(`MM` in the source) become `null`. The buoy has no tide reading, so the tide chart has
no buoy series. Buoys are listed in `BUOYS` in both `scripts/download_buoy.py` and
`js/app.js`.

### Build the manifest

After generating the wave files and any buoy files, build `data/index.json` (the
manifest the website reads to discover both):

```bash
uv run scripts/build_index.py  # scans data/ for waves_*.bin.gz and buoy_*.json
```

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
