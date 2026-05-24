"use strict";

const FEET_PER_METER = 3.28084;
const LOCAL_TIMEZONE = "America/Los_Angeles";

// Default view state (used when no URL params are provided)
const DEFAULT_MARKER_LAT = 36.6113; // Breakwater
const DEFAULT_MARKER_LON = -121.891;

// ── Color scale (blue → cyan → yellow → red) ────────────────────────────────

const MAX_WAVE_HEIGHT = 12;
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
}

// ── Heatmap rendering ────────────────────────────────────────────────────────

function buildHeatmapURL(data, tIdx) {
    const { nx, ny } = data.metadata.grid;
    const canvas = document.createElement("canvas");
    canvas.width = nx;
    canvas.height = ny;
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(nx, ny);
    const wh = data.wave_height;

    for (let y = 0; y < ny; y++) {
        for (let x = 0; x < nx; x++) {
            const h = wh[y * nx + x]?.[tIdx];
            const p = ((ny - 1 - y) * nx + x) * 4; // flip y so south=bottom
            if (h == null || isNaN(h)) {
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

async function loadData() {
    try {
        const r = await fetch("data/waves.json");
        if (!r.ok) throw new Error();
        return await r.json();
    } catch {
        return generateEmptyData();
    }
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
            units: { wave_height: "m", wave_dir: "°", wave_period: "s", water_level: "m" },
        },
        wave_height: [],
        wave_dir: [],
        wave_period: [],
        water_level: [],
        _demo: true,
    };
}

function initData(data) {
    for (const series of data.wave_height) {
        for (let i = 0; i < series.length; i++)
            if (series[i] != null) series[i] = +(series[i] * FEET_PER_METER).toFixed(2);
    }
    for (const series of data.water_level) {
        for (let i = 0; i < series.length; i++)
            if (series[i] != null) series[i] = +(series[i] * FEET_PER_METER).toFixed(2);
    }
    for (const series of data.wave_dir) {
        // need to add 180° because `wave_dir` points toward the wave origin
        for (let i = 0; i < series.length; i++) if (series[i] != null) series[i] = (series[i] + 180) % 360;
    }
}

// ── Charts ───────────────────────────────────────────────────────────────────

const GRID_COLOR = "#1d3556";
const TICK_COLOR = "#527090";

// Plugin: yellow dashed vertical line at current time
Chart.register({
    id: "timeCursor",
    afterDatasetsDraw(chart) {
        const idx = chart._currentIdx;
        if (idx == null || !chart.scales.x) return;
        const x = chart.scales.x.getPixelForValue(idx);
        const { top, bottom, left, right } = chart.chartArea;
        if (x < left || x > right) return;
        const ctx = chart.ctx;
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(x, top);
        ctx.lineTo(x, bottom);
        ctx.strokeStyle = "rgba(255,215,0,0.8)";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 4]);
        ctx.stroke();
        ctx.restore();
    },
});

function makeChart(id, color, yLabel, yMin, yMax, tickCb, yTickOptions = {}) {
    const cfg = {
        type: "line",
        data: {
            labels: [],
            datasets: [
                {
                    data: [],
                    borderColor: color,
                    backgroundColor: color + "22",
                    fill: true,
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 5,
                    tension: 0.3,
                    spanGaps: false,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            animation: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    mode: "index",
                    intersect: false,
                    callbacks: {
                        label: (ctx) => (ctx.parsed.y != null ? `${ctx.parsed.y.toFixed(2)} ${yLabel}` : "N/A"),
                    },
                },
            },
            scales: {
                x: {
                    ticks: {
                        color: TICK_COLOR,
                        maxTicksLimit: 6,
                        maxRotation: 0,
                        font: { size: 10 },
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

let charts = {};

function initCharts(times) {
    const labels = times.map((t) => {
        const d = new Date(t);
        return (
            d
                .toLocaleString("en-US", {
                    weekday: "short",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                    hour12: false,
                    timeZone: LOCAL_TIMEZONE,
                })
                .replace(",", "") + " PT"
        );
    });

    function heightGradient(context, alpha) {
        const chart = context.chart;
        const { ctx: c, chartArea, scales } = chart;
        if (!chartArea || !scales.x) return `rgba(74,158,218,${alpha})`;
        const data = chart.data.datasets[0].data;
        if (!data.length) return `rgba(74,158,218,${alpha})`;
        const gradient = c.createLinearGradient(chartArea.left, 0, chartArea.right, 0);
        const width = chartArea.right - chartArea.left;
        data.forEach((y, i) => {
            const t = (scales.x.getPixelForValue(i) - chartArea.left) / width;
            const [r, g, b] = y != null && !isNaN(y) ? heightToRGB(y) : [74, 158, 218];
            gradient.addColorStop(Math.max(0, Math.min(1, t)), `rgba(${r},${g},${b},${alpha})`);
        });
        return gradient;
    }

    charts.height = makeChart("chart-height", "#4a9eda", "ft", 0, null, null);
    charts.height.data.datasets[0].borderColor = (ctx) => heightGradient(ctx, 1);
    charts.height.data.datasets[0].backgroundColor = (ctx) => heightGradient(ctx, 0.66);
    charts.period = makeChart("chart-period", "#a78bfa", "sec", 0, null, null);
    charts.dir = makeChart("chart-dir", "#e07a5f", "deg", 0, 360, (v) => `${v}°`, { stepSize: 90 });
    charts.tide = makeChart("chart-tide", "#6bc49a", "ft", null, null, null);

    Object.values(charts).forEach((c) => {
        c.options.plugins.tooltip.callbacks.title = (items) => {
            if (!items.length) return "";
            return items[0].label;
        };
        c.options.scales.x.ticks.maxTicksLimit = 100;
        c.options.scales.x.ticks.callback = (val) => {
            const d = new Date(times[val]);
            return d.toLocaleString("en-US", {
                weekday: "short",
                timeZone: LOCAL_TIMEZONE,
            });
        };
        c.options.scales.x.afterBuildTicks = (scale) => {
            scale.ticks = scale.ticks.filter((tick) => {
                const d = new Date(times[tick.value]);
                const h = parseInt(
                    d.toLocaleString("en-US", {
                        hour: "2-digit",
                        hour12: false,
                        timeZone: LOCAL_TIMEZONE,
                    }),
                    10,
                );
                return h === 0;
            });
        };
    });

    Object.values(charts).forEach((c) => {
        c.data.labels = labels;
        c.update("none");
    });
}

function updateCharts(data, gridIdx, tIdx) {
    charts.height.data.datasets[0].data = data.wave_height[gridIdx] || [];
    charts.period.data.datasets[0].data = (data.wave_period || [])[gridIdx] || [];
    charts.dir.data.datasets[0].data = data.wave_dir[gridIdx] || [];
    charts.tide.data.datasets[0].data = (data.water_level || [])[gridIdx] || [];
    Object.values(charts).forEach((c) => {
        c._currentIdx = tIdx;
        c.update("none");
    });
}

function setTimeCursor(tIdx) {
    Object.values(charts).forEach((c) => {
        c._currentIdx = tIdx;
        c.update("none");
    });
}

// ── Time display ─────────────────────────────────────────────────────────────

function fmtTime(isoStr, forecastTime) {
    const d = new Date(isoStr);
    const dh = Math.round((d - new Date(forecastTime)) / 3.6e6);
    return (
        d
            .toLocaleString("en-US", {
                weekday: "short",
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                hour12: false,
                timeZone: LOCAL_TIMEZONE,
            })
            .replace(",", "") + ` PT (+${dh}h)`
    );
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

// Appended to a custom Leaflet pane (z-index 450) so it sits below marker-pane
// (600) but above the tile layer inside leaflet-map-pane's stacking context.
function initArrowOverlay(map, grid, data) {
    const arrowPane = map.createPane("arrowPane");
    arrowPane.style.zIndex = 450;
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
                const dir = data.wave_dir[gy * nx + gx]?.[i];
                if (dir == null) continue;
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
    return {
        lat: num("lat"),
        lon: num("lon"),
    };
}

function writeUrlState({ lat, lon }) {
    const p = new URLSearchParams(window.location.search);
    const set = (k, v, digits) => {
        if (v == null || isNaN(v)) p.delete(k);
        else p.set(k, digits != null ? v.toFixed(digits) : String(v));
    };
    set("lat", lat, 4);
    set("lon", lon, 4);
    const qs = p.toString();
    history.replaceState(null, "", window.location.pathname + (qs ? "?" + qs : "") + window.location.hash);
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function init() {
    const data = await loadData();
    const { times, grid } = data.metadata;
    const nt = times.length;

    initData(data);

    const urlState = readUrlState();

    document.getElementById("version-label").textContent = `v${VERSION}`;
    document.getElementById("status").textContent =
        `${data.metadata.source} · ${nt} time steps · ${grid.nx}×${grid.ny} grid`;
    if (data._demo) document.getElementById("demo-banner").style.display = "block";

    // Map — centers on the initial marker location
    const initialMarkerLat = urlState.lat != null ? urlState.lat : DEFAULT_MARKER_LAT;
    const initialMarkerLon = urlState.lon != null ? urlState.lon : DEFAULT_MARKER_LON;
    const map = L.map("map").setView([initialMarkerLat, initialMarkerLon], 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap contributors",
        maxZoom: 18,
    }).addTo(map);
    const mapBounds = [
        [grid.lat_min, grid.lon_min],
        [grid.lat_max, grid.lon_max],
    ];
    const heatLayer = L.imageOverlay("", mapBounds, { opacity: 0.8, interactive: false }).addTo(map);
    const drawArrows = initArrowOverlay(map, grid, data);

    // Time control
    const slider = document.getElementById("time-slider");
    const timeLabel = document.getElementById("time-label");
    slider.max = nt - 1;
    let tIdx = 0;

    function applyTime(i) {
        tIdx = i;
        slider.value = i;
        timeLabel.textContent = fmtTime(times[i], data.metadata.forecast_time);
        heatLayer.setUrl(buildHeatmapURL(data, i));
        drawArrows(i);
        setTimeCursor(i);
    }

    applyTime(0);
    slider.addEventListener("input", () => applyTime(+slider.value));

    let playing = false,
        timer = null;
    const playBtn = document.getElementById("play-btn");
    playBtn.addEventListener("click", () => {
        playing = !playing;
        playBtn.innerHTML = playing ? "&#9646;&#9646;" : "&#9654;";
        if (playing) {
            timer = setInterval(() => applyTime((tIdx + 1) % nt), 100);
        } else {
            clearInterval(timer);
        }
    });

    // Charts
    initCharts(times);
    drawLegend();

    // Drag-to-scrub on charts
    let chartDragging = false;
    function scrubChart(chart, clientX) {
        const { left, right } = chart.chartArea;
        const x = clientX - chart.canvas.getBoundingClientRect().left;
        const ratio = (Math.max(left, Math.min(right, x)) - left) / (right - left);
        applyTime(Math.round(ratio * (nt - 1)));
    }
    Object.values(charts).forEach((chart) => {
        const el = chart.canvas.parentNode;
        el.addEventListener("mousedown", (e) => {
            e.preventDefault();
            chartDragging = true;
            scrubChart(chart, e.clientX);
        });
        el.addEventListener("mousemove", (e) => {
            if (chartDragging) scrubChart(chart, e.clientX);
        });
    });
    window.addEventListener("mouseup", () => {
        chartDragging = false;
    });

    // Map click → select nearest point
    let marker = null;
    let selectedIdx = null;

    function selectPoint(lat, lon) {
        const pt = nearestPoint(grid, lat, lon);
        if (!marker) {
            marker = L.circleMarker([pt.lat, pt.lon], {
                radius: 8,
                color: "#fff",
                fillColor: "#ffd700",
                fillOpacity: 1,
                weight: 2,
                pane: "markerPane",
            }).addTo(map);
        } else {
            marker.setLatLng([pt.lat, pt.lon]);
        }
        document.getElementById("instructions").style.display = "none";
        document.getElementById("selected-coords").textContent =
            `${pt.lat.toFixed(4)}°N, ${Math.abs(pt.lon).toFixed(4)}°W`;
        document.getElementById("selected-info").style.display = "block";
        selectedIdx = pt.idx;
        updateCharts(data, selectedIdx, tIdx);
    }

    function syncUrl() {
        writeUrlState({
            lat: marker ? marker.getLatLng().lat : null,
            lon: marker ? marker.getLatLng().lng : null,
        });
    }

    selectPoint(initialMarkerLat, initialMarkerLon);

    map.on("click", ({ latlng: { lat, lng: lon } }) => {
        selectPoint(lat, lon);
        syncUrl();
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
}

document.addEventListener("DOMContentLoaded", init);
