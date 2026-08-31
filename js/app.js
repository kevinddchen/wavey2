"use strict";

const LOCAL_TIMEZONE = "America/Los_Angeles";

// Marker / chart series colors
const PRIMARY_COLOR = "#4285f4"; // blue — left-click marker + primary chart series
const SECONDARY_COLOR = "#ffd700"; // gold — right-click marker + comparison chart series

// Dive sites — one constant per site, then the list and defaults reference them
const SITE_BREAKWATER = { name: "Breakwater", lat: 36.6113, lon: -121.891 };
const SITE_MCABEE = { name: "McAbee", lat: 36.6158, lon: -121.8966 };
const SITE_LOVERS_POINT = { name: "Lovers Point", lat: 36.6249, lon: -121.9135 };
const SITE_MONASTERY = { name: "Monastery", lat: 36.5254, lon: -121.9303 };

const DIVE_SITES = [SITE_BREAKWATER, SITE_MCABEE, SITE_LOVERS_POINT, SITE_MONASTERY];

// NDBC buoys with real measurements, overlaid on the charts via the selector
// dropdowns. The data file is timestamped for cache-busting, so its name isn't
// hardcoded here — it's resolved at runtime from `data/index.json` by id (a buoy
// only appears in the selectors if the manifest lists a file for it). Unlike dive
// sites, a buoy is a fixed observation point (not a forecast grid cell), so its
// data is a time series aligned to the forecast time axis.
const BUOYS = [
    { id: "46236", name: "Buoy 46236 Measurements", lat: 36.759, lon: -121.95 },
    { id: "46239", name: "Buoy 46239 Measurements", lat: 36.342, lon: -122.11 },
];

// Default primary marker (used when no `lat`/`lon` URL params are provided).
// The comparison marker is only shown if `cmpLat`/`cmpLon` URL params are present.
const DEFAULT_PRIMARY_SITE = SITE_BREAKWATER;

// Esri's light gray canvas. Labels ship as a separate transparent overlay.
const MAP_PROVIDER_URL =
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}";
const MAP_PROVIDER_LABELS_URL =
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}";
const MAP_PROVIDER_ATTRIBUTION = "&copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors";

// Map zoom — `DEFAULT_ZOOM` is used when no `zoom` URL param is provided.
// Tiles only exist through z16; past that Esri serves a "Map data not yet available" placeholder
const DEFAULT_ZOOM = 11;
const MAX_ZOOM = 16;

// z-index of the custom Leaflet panes, listed bottom to top alongside the built-in
// panes they interleave with, so the map's whole stacking order reads in one place.
// Keys are the pane names passed to `map.createPane`.
//
//   tilePane      200  (Leaflet)  basemap tiles
//   overlayPane   400  (Leaflet)  wave-height heatmap
//   labelPane     425             place labels — above the heatmap, below the data
//   arrowPane     450             wave-direction arrows
//   markerPane    600  (Leaflet)  comparison marker, buoys, dive sites
//   primaryMarkerPane 700         the primary marker, above all other markers
const PANE_Z = {
    labelPane: 425,
    arrowPane: 450,
    primaryMarkerPane: 700,
};

// Valid values for the `charts` URL param (matches the `data-chart` attribute
// on each `.chart-container` in index.html).
const CHART_NAMES = ["height", "period", "dir", "tide"];

// Display units — set once at startup via `setUnits` based on the `units` URL
// param ("ft" default, "m" for meters). NWPS data arrives in meters, so the
// scale factor is applied in `initData`.
let UNIT = "ft";
let UNIT_SCALE = 3.28084;
let MAX_WAVE_HEIGHT = 12; // upper bound of the wave-height color scale, in `UNIT`

function setUnits(units) {
    if (units === "ft") {
        UNIT = "ft";
        UNIT_SCALE = 3.28084;
        MAX_WAVE_HEIGHT = 12;
    } else if (units === "m") {
        UNIT = "m";
        UNIT_SCALE = 1;
        MAX_WAVE_HEIGHT = 4;
    } else {
        console.error(`setUnits: invalid value ${JSON.stringify(units)}, expected "m" or "ft"`);
    }
}

// ── Color scale (blue → cyan → yellow → red) ────────────────────────────────
const STOPS = [
    [10, 40, 180], // deep blue
    [20, 110, 255], // bright blue
    [0, 190, 240], // sky blue
    [0, 215, 185], // teal
    [80, 205, 90], // green
    [225, 215, 0], // yellow
    [255, 135, 0], // amber
    [235, 45, 20], // red-orange
    [160, 0, 0], // dark red
];

function heightToRGB(h) {
    const t = Math.min(Math.max(h, 0) / MAX_WAVE_HEIGHT, 1);
    const fi = t * (STOPS.length - 1);
    const lo = Math.floor(fi);
    const hi = Math.min(lo + 1, STOPS.length - 1);
    const f = fi - lo;
    return STOPS[lo].map((v, i) => Math.round(v * (1 - f) + STOPS[hi][i] * f));
}

function drawLegend() {
    const canvas = document.getElementById("legend-canvas");
    const ctx = canvas.getContext("2d");
    const gradient = ctx.createLinearGradient(0, 0, canvas.width, 0);
    for (let i = 0; i <= MAX_WAVE_HEIGHT; i++) {
        const [r, g, b] = heightToRGB(i);
        gradient.addColorStop(i / MAX_WAVE_HEIGHT, `rgb(${r},${g},${b})`);
    }
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    const labels = document.querySelector("#legend .legend-labels");
    if (labels) {
        const mid = MAX_WAVE_HEIGHT / 2;
        labels.innerHTML = `<span>0 ${UNIT}</span><span>${mid} ${UNIT}</span><span>${MAX_WAVE_HEIGHT}+ ${UNIT}</span>`;
    }
}

// ── Heatmap rendering ────────────────────────────────────────────────────────

// Opacity of the heatmap over the water. It is clipped to the coastline (see
// `applyWaterClip`), so this trades off against the basemap underneath rather
// than against burying the shoreline.
const HEAT_OPACITY = 0.8;

// How many grid cells of wave height to spread onto the land side of the
// shoreline. Land cells are null in the forecast and Leaflet stretches the image
// ~500 m per cell, so without this the browser's interpolation fades the color
// out over the last half kilometer of water — right where the clip cuts, leaving
// a washed-out strip along the shore. Whatever spreads past the coastline is
// clipped away, so this is only worth doing when there is a clip: unclipped, it
// would be the thing painting over land.
const HEAT_DILATE_CELLS = 2;

// Wave height per grid cell at `tIdx` (NaN where there is none), with the values
// along the shoreline dilated `dilateCells` cells into the land.
function heatmapField(data, tIdx, dilateCells) {
    const { nx, ny } = data.metadata.grid;
    const wh = data.wave_height;
    let field = new Float32Array(nx * ny);
    for (let c = 0; c < field.length; c++) {
        const h = wh[c]?.[tIdx];
        field[c] = h == null ? NaN : h;
    }

    for (let pass = 0; pass < dilateCells; pass++) {
        const src = field;
        field = src.slice(); // each pass reads the previous one, so a cell filled now can't seed this pass
        for (let y = 0; y < ny; y++) {
            for (let x = 0; x < nx; x++) {
                const c = y * nx + x;
                if (!isNaN(src[c])) continue;
                let sum = 0;
                let n = 0;
                for (let dy = -1; dy <= 1; dy++) {
                    for (let dx = -1; dx <= 1; dx++) {
                        const yy = y + dy;
                        const xx = x + dx;
                        if (yy < 0 || yy >= ny || xx < 0 || xx >= nx) continue;
                        const v = src[yy * nx + xx];
                        if (!isNaN(v)) {
                            sum += v;
                            n++;
                        }
                    }
                }
                if (n > 0) field[c] = sum / n;
            }
        }
    }
    return field;
}

function buildHeatmapURL(data, tIdx, dilateCells) {
    const { nx, ny } = data.metadata.grid;
    const canvas = document.createElement("canvas");
    canvas.width = nx;
    canvas.height = ny;
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(nx, ny);
    const field = heatmapField(data, tIdx, dilateCells);

    for (let y = 0; y < ny; y++) {
        for (let x = 0; x < nx; x++) {
            const h = field[y * nx + x];
            const p = ((ny - 1 - y) * nx + x) * 4; // flip y so south=bottom
            if (isNaN(h)) {
                img.data[p + 3] = 0;
                continue;
            }
            const [r, g, b] = heightToRGB(h);
            img.data[p] = r;
            img.data[p + 1] = g;
            img.data[p + 2] = b;
            img.data[p + 3] = 255;
        }
    }
    ctx.putImageData(img, 0, 0);
    return canvas.toDataURL();
}

// Clip the heatmap overlay to the water, so it never paints over land whatever
// its opacity. Leaflet places the overlay by projecting the grid's corners
// and stretching the image between them, so the coastline in `js/coastline.js` is
// pre-projected the same way — SVG objectBoundingBox units, x linear in longitude
// and y linear in Web Mercator northing — and lands exactly on the basemap's own
// coastline. (Spacing it linearly in latitude instead misses by up to 115 m over
// the grid's 0.8° of latitude.) The browser applies the clip when compositing, so
// it costs nothing per frame and survives every `setUrl`, pan and zoom.
//
// The empty <clipPath> it fills in lives in index.html; the path itself is the
// unit square minus the land, hence the `clip-rule="evenodd"` there. The path is
// built for the grid bounds it records, so regenerate it if those ever change.
//
// Returns whether the overlay ended up clipped, which is also what decides whether
// the shoreline dilation is worth doing.
function applyWaterClip(img) {
    // `COASTLINE` comes from a separate <script>. If that failed to load, fall back
    // to the heatmap as it was before any of this existed — unclipped, undilated,
    // and readable over land at `HEAT_OPACITY` — rather than taking down the rest
    // of `init`, which wires up the charts and time control after this point.
    if (typeof COASTLINE === "undefined") {
        console.warn("js/coastline.js did not load; leaving the heatmap unclipped");
        return false;
    }

    document.getElementById("water-clip-path").setAttribute("d", COASTLINE.path);
    img.style.clipPath = "url(#water-clip)";
    return true;
}

// ── Grid lookup ──────────────────────────────────────────────────────────────

function nearestPoint(grid, lat, lon) {
    const x = Math.round(((lon - grid.lon_min) / (grid.lon_max - grid.lon_min)) * (grid.nx - 1));
    const y = Math.round(((lat - grid.lat_min) / (grid.lat_max - grid.lat_min)) * (grid.ny - 1));
    const cx = Math.max(0, Math.min(grid.nx - 1, x));
    const cy = Math.max(0, Math.min(grid.ny - 1, y));
    return {
        idx: cy * grid.nx + cx,
        lat: grid.lat_min + (cy / (grid.ny - 1)) * (grid.lat_max - grid.lat_min),
        lon: grid.lon_min + (cx / (grid.nx - 1)) * (grid.lon_max - grid.lon_min),
    };
}

// ── Data loading ─────────────────────────────────────────────────────────────

// Fetch + gunzip + decode a single forecast binary at `path`.
// Pre-gzipped so the wire transfer stays small even when the host doesn't
// auto-compress application/octet-stream (e.g. GitHub Pages).
async function fetchForecast(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`fetch ${path}: ${r.status}`);
    const decompressed = r.body.pipeThrough(new DecompressionStream("gzip"));
    const buf = await new Response(decompressed).arrayBuffer();
    return decodeBinary(buf);
}

// Resolve which forecast run to show and decode it, reading the manifest
// (`data/index.json`, a `{ forecasts, buoys }` object). Falls back to empty data
// if the manifest is missing, empty, or malformed, or if the chosen run's binary
// fails to load. Returns { data, forecasts, buoys, selectedId }: `forecasts` /
// `buoys` are the manifest lists (empty when falling back) and `selectedId` is the
// chosen run's id (or null). `buoys` is `[{ id, file }]` (see build_index.py).
async function loadData(forecastId) {
    try {
        // `no-store` bypasses the HTTP cache so a single refresh always sees the
        // latest manifest. index.json has a fixed URL (unlike the timestamped,
        // content-addressed binaries it points to), so a cached copy would
        // otherwise pin the app to a stale run until the cache expired.
        const r = await fetch("data/index.json", { cache: "no-store" });
        if (!r.ok) throw new Error();
        const manifest = await r.json();
        const forecasts = manifest.forecasts;
        if (!Array.isArray(forecasts) || forecasts.length === 0) throw new Error();
        const buoys = Array.isArray(manifest.buoys) ? manifest.buoys : [];
        const sel = forecasts.find((f) => f.id === forecastId) || forecasts[0];
        const data = await fetchForecast("data/" + sel.file);
        return { data, forecasts, buoys, selectedId: sel.id };
    } catch {
        return { data: generateEmptyData(), forecasts: [], buoys: [], selectedId: null };
    }
}

// Fetch + decode one buoy's observation file, returning `{ ...entry, ...columnar
// data }` (parallel `times` / `wave_height` / `wave_period` / `wave_dir` arrays;
// see scripts/download_buoy.py) prepared by `initBuoy`. Like switching forecast
// runs, this is lazy — the file is only fetched when the buoy is selected.
// Throws if the file is missing or malformed.
async function fetchBuoy(entry) {
    const r = await fetch("data/" + entry.file);
    if (!r.ok) throw new Error(`fetch ${entry.file}: ${r.status}`);
    const json = await r.json();
    if (!Array.isArray(json.times)) throw new Error(`bad buoy file: ${entry.file}`);
    const buoy = { ...entry, ...json };
    initBuoy(buoy);
    return buoy;
}

// Warm the browser HTTP cache by downloading the other runs' binaries and the
// buoy files in the background, so a later `setForecast` / buoy selection hits
// cache instead of the network. Best-effort: runs when idle, sequential to avoid
// competing with the user's requests, silent on failure, and only drains the raw
// bytes (no decode). `currentId` (the already-loaded run) is skipped. `buoys` is
// the manifest list `[{ id, file }]` (the files are timestamped, see build_index.py).
function prefetchRuns(forecasts, currentId, buoys) {
    const drain = async (path) => {
        try {
            const r = await fetch(path);
            await r.arrayBuffer(); // drain into the cache
        } catch {
            // best-effort; a failed prefetch just means a slower switch later
        }
    };
    const start = async () => {
        for (const f of forecasts) {
            if (f.id !== currentId) await drain("data/" + f.file);
        }
        for (const b of buoys) {
            await drain("data/" + b.file);
        }
    };
    if (typeof requestIdleCallback === "function") requestIdleCallback(start);
    else setTimeout(start, 0);
}

// Binary format: [u32 LE header_len][JSON header][typed LE arrays...]
// See scripts/grib2bin.py for the writer.
const DTYPE_VIEW = {
    uint8: { ctor: Uint8Array, bytes: 1 },
    int8: { ctor: Int8Array, bytes: 1 },
    int16: { ctor: Int16Array, bytes: 2 },
};

// Inverse of the encode-side transform (linear → identity, sqrt → square).
const INVERSE_TRANSFORM = {
    linear: (y) => y,
    sqrt: (y) => y * y,
};

function decodeBinary(buf) {
    const view = new DataView(buf);
    const headerLen = view.getUint32(0, true);
    const headerStr = new TextDecoder().decode(new Uint8Array(buf, 4, headerLen));
    const header = JSON.parse(headerStr);
    const { metadata, ncells, nt, variables } = header;

    let byteOffset = 4 + headerLen; // 4-byte aligned by header padding
    const data = { metadata };
    for (const v of variables) {
        const info = DTYPE_VIEW[v.dtype];
        if (!info) throw new Error("unsupported dtype: " + v.dtype);
        const transform = v.transform;
        if (!INVERSE_TRANSFORM[transform]) throw new Error("unsupported transform: " + transform);
        const count = ncells * nt;
        const raw = new info.ctor(buf, byteOffset, count);
        byteOffset += count * info.bytes;

        // Decode into one flat Float32Array (NaN = missing), branching on the
        // transform once so the inner loop holds no indirect call. Each cell's
        // series is a zero-copy subarray view over this buffer.
        const { scale, sentinel } = v;
        const flat = new Float32Array(count);
        if (transform === "linear") {
            for (let i = 0; i < count; i++) {
                const x = raw[i];
                flat[i] = x === sentinel ? NaN : x / scale;
            }
        } else if (transform === "sqrt") {
            for (let i = 0; i < count; i++) {
                const x = raw[i];
                const y = x / scale;
                flat[i] = x === sentinel ? NaN : y * y;
            }
        } else {
            throw new Error("unexpected transform: " + transform);
        }
        const series = new Array(ncells);
        for (let c = 0; c < ncells; c++) {
            const base = c * nt;
            series[c] = flat.subarray(base, base + nt);
        }
        data[v.name] = series;
    }
    return data;
}

function generateEmptyData() {
    const nt = 145; // usual number of timesteps
    const t0 = new Date();
    const times = Array.from({ length: nt }, (_, i) => new Date(t0.getTime() + i * 3.6e6).toISOString());

    return {
        metadata: {
            source: "No data",
            forecast_time: t0.toISOString(),
            times,
            grid: { nx: 90, ny: 178, lat_min: 36.2, lat_max: 37.0, lon_min: -122.2, lon_max: -121.7 },
            units: { wave_height: "m", wave_dir: "deg", wave_period: "s", water_level: "m" },
        },
        wave_height: [],
        wave_dir: [],
        wave_period: [],
        water_level: [],
    };
}

function initData(data) {
    for (const series of data.wave_height) {
        for (let i = 0; i < series.length; i++) if (series[i] != null) series[i] *= UNIT_SCALE;
    }
    for (const series of data.water_level) {
        for (let i = 0; i < series.length; i++) if (series[i] != null) series[i] *= UNIT_SCALE;
    }
    for (const series of data.wave_dir) {
        // need to add 180° because `wave_dir` points toward the wave origin
        for (let i = 0; i < series.length; i++) if (series[i] != null) series[i] = (series[i] + 180) % 360;
    }
}

// Prepare a loaded buoy in place: parse the timestamp column to epoch-ms and apply
// the same conventions `initData` applies to the forecast — scale `wave_height`
// to the display unit and rotate `wave_dir` +180° (the buoy reports the direction
// waves come *from*; the charts show the direction waves travel *toward*). Missing
// values stay null. `wave_period` needs no transform. The columns are parallel
// arrays aligned to `times`, oldest-first (see scripts/download_buoy.py).
function initBuoy(buoy) {
    buoy.wave_height = buoy.wave_height.map((v) => (v == null ? null : v * UNIT_SCALE));
    buoy.wave_dir = buoy.wave_dir.map((v) => (v == null ? null : (v + 180) % 360));
    // Index each observation by its epoch-ms timestamp. Both the buoy and the
    // forecast sit on the same hourly grid (see scripts/download_buoy.py), so
    // `alignBuoy` can match forecast steps to observations by exact timestamp.
    buoy.indexByTime = new Map(buoy.times.map((t, i) => [Date.parse(t), i]));
}

// Align a buoy's observations to the forecast time axis. Returns
// `{ wave_height, wave_period, wave_dir }`, each an array aligned to `times` (so
// it drops straight into a chart dataset like a grid cell's series). Each forecast
// step takes the observation at the same timestamp, else null (a gap, drawn as a
// break since the datasets use `spanGaps:false`). The buoy's past observations only
// overlap the forecast steps near the run's analysis time, so older forecast runs
// fill more of the axis than newer ones.
function alignBuoy(buoy, times) {
    const out = {
        wave_height: new Array(times.length).fill(null),
        wave_period: new Array(times.length).fill(null),
        wave_dir: new Array(times.length).fill(null),
    };
    for (let t = 0; t < times.length; t++) {
        const j = buoy.indexByTime.get(Date.parse(times[t]));
        if (j !== undefined) {
            out.wave_height[t] = buoy.wave_height[j];
            out.wave_period[t] = buoy.wave_period[j];
            out.wave_dir[t] = buoy.wave_dir[j];
        }
    }
    return out;
}

// ── Charts ───────────────────────────────────────────────────────────────────

const GRID_COLOR = "#1d3556";
const TICK_COLOR = "#527090";

// Plugin: yellow dashed vertical line at the slider's current time, plus a
// fainter solid line at the client's wall-clock "now" (if it falls within the
// forecast window). `_currentIdx` (integer) and `_nowIdx` (fractional, or null)
// are set on the chart instance.
Chart.register({
    id: "timeCursor",
    afterDatasetsDraw(chart) {
        if (!chart.scales.x) return;
        const { top, bottom, left, right } = chart.chartArea;
        const ctx = chart.ctx;

        const drawLine = (idx, color, width, dash) => {
            if (idx == null) return;
            const x = chart.scales.x.getPixelForValue(idx);
            if (x < left || x > right) return;
            ctx.save();
            ctx.beginPath();
            ctx.moveTo(x, top);
            ctx.lineTo(x, bottom);
            ctx.strokeStyle = color;
            ctx.lineWidth = width;
            ctx.setLineDash(dash);
            ctx.stroke();
            ctx.restore();
        };

        drawLine(chart._nowIdx, "rgba(109,184,232,0.5)", 1, [3, 3]);
        drawLine(chart._currentIdx, "rgba(255,215,0,0.8)", 1.5, [4, 4]);
    },
});

function makeChart(id, times, yLabel, yMin, yMax, tickCb, yTickOptions = {}) {
    const dataset = (clr) => ({
        data: [],
        borderColor: clr,
        backgroundColor: clr + "22",
        fill: false,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 5,
        tension: 0.3,
        spanGaps: false,
    });
    const labels = times.map((t) => `${formatLocalTime(new Date(t), FULL_TIME_FIELDS)} PT`);
    const cfg = {
        type: "line",
        data: {
            labels,
            datasets: [dataset(PRIMARY_COLOR), dataset(SECONDARY_COLOR)],
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            animation: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    mode: "nearest",
                    intersect: false,
                    position: "nearest",
                    callbacks: {
                        title: (items) => (items.length ? items[0].label : ""),
                        label: (ctx) => (ctx.parsed.y != null ? `${ctx.parsed.y.toFixed(2)} ${yLabel}` : "N/A"),
                    },
                },
            },
            scales: {
                x: {
                    ticks: {
                        color: TICK_COLOR,
                        maxTicksLimit: 100,
                        maxRotation: 0,
                        font: { size: 10 },
                        callback: (val) => formatLocalTime(new Date(times[val]), { weekday: "short" }),
                    },
                    // Only show one tick per day (at local midnight)
                    afterBuildTicks: (scale) => {
                        scale.ticks = scale.ticks.filter((tick) => {
                            const h = parseInt(formatLocalTime(new Date(times[tick.value]), { hour: "2-digit" }), 10);
                            return h === 0;
                        });
                    },
                    grid: { color: GRID_COLOR },
                },
                y: {
                    min: yMin === null ? undefined : yMin,
                    max: yMax === null ? undefined : yMax,
                    title: {
                        display: true,
                        text: yLabel,
                        color: TICK_COLOR,
                        font: { size: 10 },
                    },
                    ticks: {
                        color: TICK_COLOR,
                        font: { size: 10 },
                        ...(tickCb ? { callback: tickCb } : {}),
                        ...yTickOptions,
                    },
                    grid: { color: GRID_COLOR },
                },
            },
        },
    };
    return new Chart(document.getElementById(id), cfg);
}

// Fractional index into `times` for the client's current wall-clock time, or
// null if "now" is outside the forecast window.
function nowFractionalIdx(times) {
    const now = Date.now();
    const ts = times.map((t) => new Date(t).getTime());
    if (now < ts[0] || now > ts[ts.length - 1]) return null;
    for (let i = 0; i < ts.length - 1; i++) {
        if (now >= ts[i] && now <= ts[i + 1]) return i + (now - ts[i]) / (ts[i + 1] - ts[i]);
    }
    return null;
}

function initCharts(times) {
    const charts = {
        height: makeChart("chart-height", times, UNIT, 0, null, null),
        period: makeChart("chart-period", times, "sec", 0, null, null),
        dir: makeChart("chart-dir", times, "deg", 0, 360, (v) => `${v}°`, { stepSize: 90 }),
        tide: makeChart("chart-tide", times, UNIT, null, null, null),
    };
    const nowIdx = nowFractionalIdx(times);
    Object.values(charts).forEach((c) => (c._nowIdx = nowIdx));
    return charts;
}

// Refresh both series on every chart. Each `src` is a per-slot source descriptor:
//   null            → empty (no marker for that slot)
//   { idx }         → forecast grid cell `idx` (read live from `data`)
//   { series, ... } → a buoy, pre-aligned to the time axis (see `alignBuoy`)
function updateCharts(charts, data, src1, src2, tIdx) {
    const seriesFor = (src, key) => {
        if (!src) return [];
        if (src.series) return src.series[key] || [];
        return (data[key] || [])[src.idx] || [];
    };
    const apply = (chart, key) => {
        chart.data.datasets[0].data = seriesFor(src1, key);
        chart.data.datasets[1].data = seriesFor(src2, key);
    };
    apply(charts.height, "wave_height");
    apply(charts.period, "wave_period");
    apply(charts.dir, "wave_dir");
    apply(charts.tide, "water_level");
    Object.values(charts).forEach((c) => {
        c._currentIdx = tIdx;
        c.update("none");
    });
}

function setTimeCursor(charts, tIdx) {
    // Data hasn't changed — only the cursor plugin needs to redraw. `render()`
    // skips the update cycle (no re-layout / re-scale) that `update("none")`
    // does, which matters during playback (40 redraws/sec across 4 charts).
    Object.values(charts).forEach((c) => {
        c._currentIdx = tIdx;
        c.render();
    });
}

// ── Time display ─────────────────────────────────────────────────────────────

const FULL_TIME_FIELDS = {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
};

function formatLocalTime(date, fields) {
    return date.toLocaleString("en-US", { ...fields, hour12: false, timeZone: LOCAL_TIMEZONE }).replace(",", "");
}

function formatAge(ms) {
    const h = Math.round(ms / 3.6e6);
    if (h < 1) return "just issued";
    if (h < 24) return `${h}h ago`;
    return `${Math.round(h / 24)}d ago`;
}

// Sets the status label to the forecast's age (e.g. "Forecast 3h ago").
function setStatus(forecastTime) {
    const age = formatAge(Date.now() - new Date(forecastTime).getTime());
    document.getElementById("status").textContent = `Forecast ${age}`;
}

function fmtTime(isoStr, forecastTime) {
    const d = new Date(isoStr);
    const dh = Math.round((d - new Date(forecastTime)) / 3.6e6);
    return `${formatLocalTime(d, FULL_TIME_FIELDS)} PT (+${dh}h)`;
}

// ── Arrow overlay ─────────────────────────────────────────────────────────────

function drawArrow(ctx, x, y, deg, len = 10) {
    const rad = (deg * Math.PI) / 180;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(rad);
    ctx.beginPath();
    ctx.moveTo(0, -len);
    ctx.lineTo(len * 0.35, len * 0.3);
    ctx.lineTo(0, 0);
    ctx.lineTo(-len * 0.35, len * 0.3);
    ctx.closePath();
    ctx.fillStyle = "rgba(0,0,0,0.5)";
    ctx.fill();
    ctx.restore();
}

// Appended to a custom Leaflet pane (see `PANE_Z`) so it sits below the marker
// panes but above the tile layer inside leaflet-map-pane's stacking context.
// `getData` returns the currently displayed forecast (it can change when the
// user switches runs); the overlay owns a map pane + move handlers, so it's
// created once and always reads the live data rather than being recreated.
function initArrowOverlay(map, grid, getData) {
    const arrowPane = map.createPane("arrowPane");
    arrowPane.style.zIndex = PANE_Z.arrowPane;
    arrowPane.style.pointerEvents = "none";
    const arrowCanvas = document.createElement("canvas");
    arrowCanvas.style.cssText = "position:absolute;top:0;left:0;pointer-events:none";
    arrowPane.appendChild(arrowCanvas);

    function sizeArrowCanvas() {
        const c = map.getContainer();
        const cr = c.getBoundingClientRect();
        const pr = map.getPanes().mapPane.getBoundingClientRect();
        arrowCanvas.width = c.clientWidth;
        arrowCanvas.height = c.clientHeight;
        arrowCanvas.style.left = -(pr.left - cr.left) + "px";
        arrowCanvas.style.top = -(pr.top - cr.top) + "px";
    }

    let arrowTIdx = 0;
    function drawArrows(i) {
        arrowTIdx = i;
        const actx = arrowCanvas.getContext("2d");
        actx.clearRect(0, 0, arrowCanvas.width, arrowCanvas.height);
        const { nx, ny, lat_min, lat_max, lon_min, lon_max } = grid;
        const step = 8; // draw an arrow every `step` grid points
        for (let gy = 0; gy < ny; gy += step) {
            for (let gx = 0; gx < nx; gx += step) {
                const dir = getData().wave_dir[gy * nx + gx]?.[i];
                if (dir == null || isNaN(dir)) continue;
                const lat = lat_min + (gy / (ny - 1)) * (lat_max - lat_min);
                const lon = lon_min + (gx / (nx - 1)) * (lon_max - lon_min);
                const pt = map.latLngToContainerPoint([lat, lon]);
                drawArrow(actx, pt.x, pt.y, dir);
            }
        }
    }

    sizeArrowCanvas();
    map.on("move zoom resize", () => {
        sizeArrowCanvas();
        drawArrows(arrowTIdx);
    });

    return drawArrows;
}

// ── URL state ────────────────────────────────────────────────────────────────

function readUrlState() {
    const p = new URLSearchParams(window.location.search);
    const num = (k) => {
        const v = p.get(k);
        if (v == null) return null;
        const n = parseFloat(v);
        return isFinite(n) ? n : null;
    };
    // Treat the param as on when present, except for explicit "0" / "false".
    const bool = (k) => {
        const v = p.get(k);
        if (v == null) return false;
        return v !== "0" && v.toLowerCase() !== "false";
    };
    // Comma-separated whitelist, filtered to recognized values; null if absent.
    const list = (k, valid) => {
        const v = p.get(k);
        if (v == null) return null;
        return v
            .split(",")
            .map((s) => s.trim())
            .filter((s) => valid.includes(s));
    };
    const t = num("t");
    const zoom = num("zoom");
    return {
        lat: num("lat"),
        lon: num("lon"),
        cmpLat: num("cmpLat"),
        cmpLon: num("cmpLon"),
        zoom: zoom != null ? Math.max(0, Math.min(MAX_ZOOM, Math.round(zoom))) : null,
        forecast: p.get("forecast"),
        t: t != null ? Math.max(0, Math.round(t)) : null,
        play: bool("play"),
        units: p.get("units"),
        charts: list("charts", CHART_NAMES),
        hideMap: bool("hideMap"),
        hideSidebar: bool("hideSidebar"),
        hideHeader: bool("hideHeader"),
        hideSelectors: bool("hideSelectors"),
        hideTimeControl: bool("hideTimeControl"),
        hideFooter: bool("hideFooter"),
        disablePrefetch: bool("disablePrefetch"),
    };
}

function writeUrlState({ lat, lon, cmpLat, cmpLon, zoom, forecast }) {
    const p = new URLSearchParams(window.location.search);
    const set = (k, v, digits) => {
        if (v == null || isNaN(v)) p.delete(k);
        else p.set(k, digits != null ? v.toFixed(digits) : String(v));
    };
    set("lat", lat, 4);
    set("lon", lon, 4);
    set("cmpLat", cmpLat, 4);
    set("cmpLon", cmpLon, 4);
    set("zoom", zoom);
    // `forecast` is a string run id, not numeric — set it directly.
    if (forecast == null) p.delete("forecast");
    else p.set("forecast", forecast);

    // NOTE: some URL params are sorted in a particular order, then the others follow alphahetically
    const order = ["lat", "lon", "cmpLat", "cmpLon", "zoom", "forecast"];
    const rank = (k) => (order.indexOf(k) === -1 ? order.length : order.indexOf(k));
    const sorted = [...p].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
    const qs = new URLSearchParams(sorted).toString();
    history.replaceState(null, "", window.location.pathname + (qs ? "?" + qs : "") + window.location.hash);
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function init() {
    const urlState = readUrlState();
    if (urlState.units != null) setUnits(urlState.units);

    // Kick off the forecast download now (without awaiting) so it overlaps with
    // the data-independent DOM + map setup below — in particular the map's basemap
    // tiles, which start downloading the moment the map is created. Only the
    // data-dependent overlays (heatmap, arrows, charts) wait on `dataPromise`.
    const dataPromise = loadData(urlState.forecast);

    document.body.classList.toggle("hide-map", urlState.hideMap);
    document.body.classList.toggle("hide-sidebar", urlState.hideSidebar);
    document.body.classList.toggle("hide-header", urlState.hideHeader);
    document.body.classList.toggle("hide-selectors", urlState.hideSelectors);
    document.body.classList.toggle("hide-time-control", urlState.hideTimeControl);
    document.body.classList.toggle("hide-footer", urlState.hideFooter);

    if (urlState.charts) {
        for (const el of document.querySelectorAll(".chart-container[data-chart]")) {
            if (!urlState.charts.includes(el.dataset.chart)) el.style.display = "none";
        }
    }

    document.getElementById("version-label").textContent = `v${VERSION}`;

    // Map — centers on the initial marker location (from URL params or the default
    // site, never from forecast data), so it's built before the data is awaited and
    // its tiles download in parallel with the forecast binary.
    const initialMarkerLat = urlState.lat != null ? urlState.lat : DEFAULT_PRIMARY_SITE.lat;
    const initialMarkerLon = urlState.lon != null ? urlState.lon : DEFAULT_PRIMARY_SITE.lon;
    const map = L.map("map").setView(
        [initialMarkerLat, initialMarkerLon],
        urlState.zoom != null ? urlState.zoom : DEFAULT_ZOOM,
    );
    L.tileLayer(MAP_PROVIDER_URL, {
        attribution: MAP_PROVIDER_ATTRIBUTION,
        maxZoom: MAX_ZOOM,
    }).addTo(map);
    // Place labels ride in their own pane (see `PANE_Z`) above the heatmap, so coastal
    // town names stay readable through it, but below the arrows and markers so the
    // forecast data still wins. `pointerEvents: none` keeps the tiles from swallowing
    // the map clicks that select a grid cell.
    const labelPane = map.createPane("labelPane");
    labelPane.style.zIndex = PANE_Z.labelPane;
    labelPane.style.pointerEvents = "none";
    L.tileLayer(MAP_PROVIDER_LABELS_URL, {
        pane: "labelPane",
        maxZoom: MAX_ZOOM,
    }).addTo(map);
    // Primary marker sits on a dedicated pane above the default markerPane (see `PANE_Z`)
    map.createPane("primaryMarkerPane").style.zIndex = PANE_Z.primaryMarkerPane;

    // Everything below needs the forecast data. `data`, `selectedId`, `times`, and
    // `nt` are reassigned by `setForecast` when the user switches runs in place.
    // `grid` is constant across CG3 runs.
    const loaded = await dataPromise;
    let data = loaded.data;
    let selectedId = loaded.selectedId;
    const forecasts = loaded.forecasts;
    const { grid } = data.metadata;
    let times = data.metadata.times;
    let nt = times.length;

    initData(data);
    setStatus(data.metadata.forecast_time);

    // Forecast selector — lists the available runs (newest first). Switching
    // swaps the data in place via `setForecast` (no page reload). Hidden unless
    // there's a choice to make (≥2 runs).
    const forecastSelect = document.getElementById("forecast-select");
    if (forecasts.length > 1) {
        for (const f of forecasts) {
            const opt = document.createElement("option");
            opt.value = f.id;
            opt.textContent = `${formatLocalTime(new Date(f.forecast_time), FULL_TIME_FIELDS)} PT`;
            forecastSelect.appendChild(opt);
        }
        forecastSelect.value = selectedId;
        forecastSelect.addEventListener("change", () => setForecast(forecastSelect.value));
    } else {
        forecastSelect.style.display = "none";
    }

    const mapBounds = [
        [grid.lat_min, grid.lon_min],
        [grid.lat_max, grid.lon_max],
    ];
    const heatLayer = L.imageOverlay("", mapBounds, { opacity: HEAT_OPACITY, interactive: false }).addTo(map);
    // Dilating past the shoreline only makes sense if the clip is there to cut it
    // back off again (see `HEAT_DILATE_CELLS`).
    const dilateCells = applyWaterClip(heatLayer.getElement()) ? HEAT_DILATE_CELLS : 0;
    const drawArrows = initArrowOverlay(map, grid, () => data);

    // Charts (rebuilt by `setForecast` when the run changes, since the x-axis
    // labels/ticks are bound to `times` at creation).
    let charts = initCharts(times);
    drawLegend();

    // Time control
    const slider = document.getElementById("time-slider");
    const timeLabel = document.getElementById("time-label");
    slider.max = nt - 1;
    let tIdx = 0;

    function applyTime(i) {
        tIdx = i;
        slider.value = i;
        timeLabel.textContent = fmtTime(times[i], data.metadata.forecast_time);
        heatLayer.setUrl(buildHeatmapURL(data, i, dilateCells));
        drawArrows(i);
        setTimeCursor(charts, i);
    }

    applyTime(urlState.t != null ? Math.min(urlState.t, nt - 1) : 0);
    slider.addEventListener("input", () => applyTime(+slider.value));

    let playing = false,
        timer = null;
    const playBtn = document.getElementById("play-btn");
    function togglePlay() {
        playing = !playing;
        playBtn.innerHTML = playing ? "&#9646;&#9646;" : "&#9654;";
        if (playing) {
            timer = setInterval(() => applyTime((tIdx + 1) % nt), 100);
        } else {
            clearInterval(timer);
        }
    }
    playBtn.addEventListener("click", togglePlay);

    // `play` URL param autostarts the animation on load.
    if (urlState.play) togglePlay();

    // Keyboard shortcuts: space = play/pause, ←/→ = step. Skip when a form
    // control is focused so it doesn't fight the slider, selects, or buttons.
    window.addEventListener("keydown", (e) => {
        if (e.target instanceof Element && e.target.matches("input, select, textarea, button")) return;
        if (e.key === " ") {
            e.preventDefault();
            togglePlay();
        } else if (e.key === "ArrowLeft") {
            e.preventDefault();
            applyTime((tIdx - 1 + nt) % nt);
        } else if (e.key === "ArrowRight") {
            e.preventDefault();
            applyTime((tIdx + 1) % nt);
        }
    });

    // Drag-to-scrub on charts
    let chartDragging = false;
    function scrubChart(chart, clientX) {
        const { left, right } = chart.chartArea;
        const x = clientX - chart.canvas.getBoundingClientRect().left;
        const ratio = (Math.max(left, Math.min(right, x)) - left) / (right - left);
        applyTime(Math.round(ratio * (nt - 1)));
    }
    // Bind to each canvas once and resolve the live chart at event time, so the
    // listeners keep working after `setForecast` rebuilds the chart instances.
    Object.values(charts).forEach(({ canvas }) => {
        const el = canvas.parentNode;
        el.addEventListener("mousedown", (e) => {
            const chart = Chart.getChart(canvas);
            if (!chart) return;
            e.preventDefault();
            chartDragging = true;
            scrubChart(chart, e.clientX);
        });
        el.addEventListener("mousemove", (e) => {
            if (!chartDragging) return;
            const chart = Chart.getChart(canvas);
            if (chart) scrubChart(chart, e.clientX);
        });
    });
    window.addEventListener("mouseup", () => {
        chartDragging = false;
    });

    // Map click → primary marker (blue). Right-click → comparison marker (gold).
    // Each slot's source (`src1`/`src2`) is a descriptor consumed by `updateCharts`:
    // null, a grid cell `{ idx }`, or a buoy `{ buoy, series }` (`series` is the
    // buoy resampled onto the current `times`; recomputed on run switch).
    let marker = null;
    let marker2 = null;
    let src1 = null;
    let src2 = null;
    const gridIdxOf = (src) => (src && src.idx != null ? src.idx : null);

    function makeMarker(lat, lon, color, pane = "markerPane") {
        return L.circleMarker([lat, lon], {
            radius: 8,
            color: "#fff",
            fillColor: color,
            fillOpacity: 1,
            weight: 2,
            pane,
        });
    }

    function selectPoint(lat, lon) {
        const pt = nearestPoint(grid, lat, lon);
        if (!marker) marker = makeMarker(pt.lat, pt.lon, PRIMARY_COLOR, "primaryMarkerPane").addTo(map);
        else marker.setLatLng([pt.lat, pt.lon]);
        document.getElementById("selected-coords").textContent =
            `${pt.lat.toFixed(4)}°N, ${Math.abs(pt.lon).toFixed(4)}°W`;
        src1 = { idx: pt.idx };
        updateCharts(charts, data, src1, src2, tIdx);
    }

    function selectPoint2(lat, lon) {
        const pt = nearestPoint(grid, lat, lon);
        if (!marker2) marker2 = makeMarker(pt.lat, pt.lon, SECONDARY_COLOR).addTo(map);
        else marker2.setLatLng([pt.lat, pt.lon]);
        src2 = { idx: pt.idx };
        updateCharts(charts, data, src1, src2, tIdx);
    }

    // Point a slot at a buoy: drop its marker at the buoy's exact location (not
    // grid-snapped) and align its observations to the current time axis.
    function selectBuoy(buoy, slot) {
        const series = alignBuoy(buoy, times);
        if (slot === 1) {
            if (!marker) marker = makeMarker(buoy.lat, buoy.lon, PRIMARY_COLOR, "primaryMarkerPane").addTo(map);
            else marker.setLatLng([buoy.lat, buoy.lon]);
            document.getElementById("selected-coords").textContent = buoy.name;
            src1 = { buoy, series };
            map.panTo([buoy.lat, buoy.lon]);
        } else {
            if (!marker2) marker2 = makeMarker(buoy.lat, buoy.lon, SECONDARY_COLOR).addTo(map);
            else marker2.setLatLng([buoy.lat, buoy.lon]);
            src2 = { buoy, series };
        }
        updateCharts(charts, data, src1, src2, tIdx);
    }

    function clearComparison() {
        if (marker2) {
            marker2.remove();
            marker2 = null;
        }
        src2 = null;
        updateCharts(charts, data, src1, src2, tIdx);
    }

    function syncUrl() {
        writeUrlState({
            lat: marker ? marker.getLatLng().lat : null,
            lon: marker ? marker.getLatLng().lng : null,
            cmpLat: marker2 ? marker2.getLatLng().lat : null,
            cmpLon: marker2 ? marker2.getLatLng().lng : null,
            zoom: map.getZoom(),
            forecast: selectedId,
        });
    }

    // Switch the displayed run in place: fetch + decode the new binary, swap the
    // data / time axis, rebuild the charts, and redraw the map overlays. The grid
    // is shared across CG3 runs, so markers, map bounds, and selected cells stay
    // valid. The current time index is preserved (clamped to the new length).
    async function setForecast(id) {
        const info = forecasts.find((f) => f.id === id);
        if (!info || id === selectedId) return;
        let next;
        try {
            next = await fetchForecast("data/" + info.file);
        } catch {
            forecastSelect.value = selectedId; // revert the dropdown on failure
            return;
        }
        initData(next);
        data = next;
        selectedId = id;
        times = data.metadata.times;
        nt = times.length;

        setStatus(data.metadata.forecast_time);

        Object.values(charts).forEach((c) => c.destroy());
        charts = initCharts(times);

        // Re-align any buoy slots to the new run's time axis (a different run
        // overlaps the buoy's observation window differently).
        if (src1?.buoy) src1 = { buoy: src1.buoy, series: alignBuoy(src1.buoy, times) };
        if (src2?.buoy) src2 = { buoy: src2.buoy, series: alignBuoy(src2.buoy, times) };

        slider.max = nt - 1;
        const i = Math.min(tIdx, nt - 1);
        updateCharts(charts, data, src1, src2, i);
        applyTime(i);
        syncUrl();
    }
    map.on("zoomend", syncUrl);

    // Resolve a buoy's current (timestamped) filename from the manifest, returning
    // the `BUOYS` entry merged with its `file` — or undefined if the manifest lists
    // no file for it (e.g. its fetch failed during the build).
    const buoyFileById = new Map(loaded.buoys.map((b) => [b.id, b.file]));
    function buoyEntryById(id) {
        const entry = BUOYS.find((b) => b.id === id);
        const file = buoyFileById.get(id);
        return entry && file ? { ...entry, file } : undefined;
    }

    // Dive site dropdowns — populate both selects with the same site options, then
    // the buoys the manifest has a file for (value `buoy:<id>`; site values are
    // numeric so they never collide). Selecting a buoy lazily fetches its file and
    // points that slot at the buoy's measurements.
    const diveSitesSelect = document.getElementById("dive-sites-select");
    const diveSitesSelect2 = document.getElementById("dive-sites-select-2");
    DIVE_SITES.forEach((site, i) => {
        for (const sel of [diveSitesSelect, diveSitesSelect2]) {
            const opt = document.createElement("option");
            opt.value = String(i);
            opt.textContent = site.name;
            sel.appendChild(opt);
        }
    });
    for (const b of BUOYS) {
        if (!buoyFileById.has(b.id)) continue; // no data file in the manifest
        for (const sel of [diveSitesSelect, diveSitesSelect2]) {
            const opt = document.createElement("option");
            opt.value = "buoy:" + b.id;
            opt.textContent = b.name;
            sel.appendChild(opt);
        }
    }
    // Wire up one slot's dropdown. Slots differ only in which source they own
    // (`src1`/`src2`, read live for the revert-on-failure path), whether picking a
    // site recenters the map (primary does, comparison doesn't), and that the
    // comparison slot also offers a "clear" option.
    function bindDiveSiteSelect(select, slot) {
        select.addEventListener("change", async () => {
            const val = select.value;
            if (val === "clear") {
                clearComparison();
                select.value = ""; // reset to the "Comparison site" placeholder
            } else if (val.startsWith("buoy:")) {
                const entry = buoyEntryById(val.slice(5));
                if (!entry) return;
                let buoy;
                try {
                    buoy = await fetchBuoy(entry); // lazy fetch; browser HTTP-caches the file
                } catch {
                    const src = slot === 1 ? src1 : src2;
                    select.value = matchingSiteValue(gridIdxOf(src)); // revert on failure
                    return;
                }
                selectBuoy(buoy, slot);
            } else {
                const site = DIVE_SITES[+val];
                if (!site) return;
                if (slot === 1) {
                    selectPoint(site.lat, site.lon);
                    map.panTo([site.lat, site.lon]); // primary recenters; comparison doesn't
                } else {
                    selectPoint2(site.lat, site.lon);
                }
            }
            syncUrl();
        });
    }
    bindDiveSiteSelect(diveSitesSelect, 1);
    bindDiveSiteSelect(diveSitesSelect2, 2);

    // Map dive sites to their snapped grid indices so a click that lands on the
    // same cell as a known site can sync the dropdown.
    const SITE_GRID_INDICES = DIVE_SITES.map((s) => nearestPoint(grid, s.lat, s.lon).idx);
    const matchingSiteValue = (gridIdx) => {
        const i = SITE_GRID_INDICES.indexOf(gridIdx);
        return i >= 0 ? String(i) : "";
    };

    selectPoint(initialMarkerLat, initialMarkerLon);
    diveSitesSelect.value = matchingSiteValue(gridIdxOf(src1));

    // Comparison marker is only placed if both URL params are provided
    if (urlState.cmpLat != null && urlState.cmpLon != null) {
        selectPoint2(urlState.cmpLat, urlState.cmpLon);
    }
    diveSitesSelect2.value = matchingSiteValue(gridIdxOf(src2));

    map.on("click", ({ latlng: { lat, lng: lon } }) => {
        selectPoint(lat, lon);
        syncUrl();
        diveSitesSelect.value = matchingSiteValue(gridIdxOf(src1));
    });
    map.on("contextmenu", (e) => {
        L.DomEvent.preventDefault(e.originalEvent);
        selectPoint2(e.latlng.lat, e.latlng.lng);
        syncUrl();
        diveSitesSelect2.value = matchingSiteValue(gridIdxOf(src2));
    });

    // Sidebar resizer (drag the divider to resize the sidebar)
    const resizer = document.getElementById("resizer");
    const app = document.getElementById("app");
    let resizing = false;
    resizer.addEventListener("mousedown", (e) => {
        e.preventDefault();
        resizing = true;
        document.body.classList.add("resizing");
    });
    window.addEventListener("mousemove", (e) => {
        if (!resizing) return;
        const minSidebar = 280;
        const minMap = 400; // enough room for play button + slider + legend
        const maxSidebar = Math.max(minSidebar, window.innerWidth - minMap);
        const w = Math.max(minSidebar, Math.min(maxSidebar, window.innerWidth - e.clientX));
        app.style.setProperty("--sidebar-width", w + "px");
        map.invalidateSize();
    });
    window.addEventListener("mouseup", () => {
        if (!resizing) return;
        resizing = false;
        document.body.classList.remove("resizing");
    });

    syncUrl(); // populate URL with current (defaults or URL-provided) values

    // Background-download the other runs and the buoy files so switching forecasts
    // or selecting a buoy hits cache. Opt-out via `disablePrefetch` (e.g. to save
    // bandwidth on metered connections).
    if (!urlState.disablePrefetch) prefetchRuns(forecasts, selectedId, loaded.buoys);
}

document.addEventListener("DOMContentLoaded", init);
