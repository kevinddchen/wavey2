# Monterey Bay Dive Conditions

[_--> live website <--_](https://kevinddchen.github.io/wavey2/)

A static webpage that visualizes wave forecast data from the NOAA Nearshore Wave Prediction System (NWPS) for the purpose of planning scuba diving trips in the Monterey Bay area.

This is a rewrite of an [older version](https://github.com/kevinddchen/wavey) of the project.

> **Disclaimer:** :robot: This project was heavily vibe-coded with Claude :robot:

## Features

- Interactive map with a wave-height heatmap overlay. Use the time slider and play button to animate through forecast time steps.
- Left-click any ocean point to display time-series charts for that location: wave height, wave period, wave direction, water level / tide.
- **Compare two dive sites:** right-click any ocean point to drop a gold comparison marker; its data is overlaid on every chart alongside the blue (primary) series
- **Dive-site shortcuts:** two dropdowns at the bottom of the sidebar list common Monterey Bay dive sites — pick one in the blue (primary) dropdown or the gold (comparison) dropdown to jump the corresponding marker there.
- **Switch forecast runs:** a dropdown in the selector row lists the available forecast runs — pick an earlier run to view a previous forecast.
- **Buoy measurements:** the same dropdowns also list nearby NDBC buoys — pick one to overlay the buoy's _actual_ observed wave height, period, and direction on the charts.
- **Keyboard shortcuts:** `Space` toggles play/pause and `←` / `→` step the time slider one frame at a time.

### Embedding on another site

The page can be embedded via `<iframe>`. A minimal example:

```html
<iframe src="https://kevinddchen.github.io/wavey2/" loading="lazy"></iframe>
```

Use the URL parameters above to deep-link to a specific location. For example, to open with the marker on Lovers Point:

```html
<iframe src="https://kevinddchen.github.io/wavey2/?lat=36.6249&lon=-121.9135" loading="lazy"></iframe>
```

### URL parameters

The current marker positions are encoded in the URL so you can bookmark or share a link to a specific view:

| Parameter         | Description                                                 |
| ----------------- | ----------------------------------------------------------- |
| `lat`             | Latitude of the blue (primary) marker                       |
| `lon`             | Longitude of the blue (primary) marker                      |
| `cmpLat`          | Latitude of the gold (comparison) marker                    |
| `cmpLon`          | Longitude of the gold (comparison) marker                   |
| `zoom`            | Initial map zoom level (integer, 9–16)                      |
| `forecast`        | Id of the forecast run to show (e.g. `20260528_1200`)       |
| `t`               | Initial time-slider index (integer)                         |
| `play`            | Autostart the time animation on load                        |
| `units`           | Display units — `ft` (default) or `m`                       |
| `charts`          | Comma-separated whitelist from `height,period,dir,tide`     |
| `hideMap`         | Hide the map panel                                          |
| `hideSidebar`     | Hide the sidebar                                            |
| `hideHeader`      | Hide the sidebar header / title block                       |
| `hideSelectors`   | Hide the forecast + dive-site selector row                  |
| `hideTimeControl` | Hide the time slider / play button / legend bar             |
| `hideFooter`      | Hide the "View on GitHub" footer link                       |
| `disablePrefetch` | Don't background-download other forecasts (saves bandwidth) |

## Data sources

The [NOAA Nearshore Wave Prediction System (NWPS)](https://polar.ncep.noaa.gov/nwps/) is a NOAA service that produces
wave forecasts for U.S. coastal areas.
A live NOAA visualization of wave heights for Monterey Bay is available
[here](https://polar.ncep.noaa.gov/nwps/nwpsloop.php?site=MTR&loop=sigwaveheight&cg=3).

Real-time wave measurements are taken from [NOAA National Data Buoy Center (NDBC)](https://www.ndbc.noaa.gov/).

## Host locally

### Requirements

```bash
uv venv
uv sync --no-dev
```

### Fetch all data (one step)

`fetch.sh` runs the whole pipeline below — download forecasts, convert them, download buoy
observations, and build the manifest:

```bash
./fetch.sh
```

### Run web server

```bash
uv run --no-dev -m http.server 8000 & open http://localhost:8000
```

A plain file server is required because the page uses `fetch()` to load `data/index.json` and the `data/*` data files, which browsers block over `file://` URLs.

### Rebuild the coastline (rarely)

The heatmap is clipped to the water so it never covers the land, using a
coastline traced from [OpenStreetMap](https://www.openstreetmap.org/copyright) and baked into `js/coastline.js`.
That file is committed and `fetch.sh` does not rebuild it — coastlines don't move. Re-run this only if the
forecast grid's bounds change:

```bash
uv run --no-dev -m wavey2.apps.build_coastline  # queries the Overpass API, rewrites js/coastline.js
```

## Hosting on GitHub Pages

1. Push this repository to GitHub.
2. Go to **Settings → Pages** and set the source to **GitHub Actions**.
3. The site will be available at `https://<username>.github.io/<repo>/`.

The `.github/workflows/deploy.yml` workflow handles the build and deploy. It runs automatically four times a day to refresh the forecast data, and can also be triggered manually from the Actions tab.

## Developer tools

### Requirements

```bash
uv venv
uv sync
npm install
```

### Python checks

```bash
uv run ty check .
uv run ruff check
uv run ruff format --check
```

### Javascript checks

```bash
npm run eslint
npm run prettier
```
