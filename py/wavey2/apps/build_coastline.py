"""
Build `js/coastline.js`, the water mask the website clips the wave heatmap to.

The heatmap is an `nx` x `ny` PNG that Leaflet stretches across the forecast
grid's lat/lon rectangle. Land cells are null in the GRIB2 data, so they are
already transparent, but the grid's ~500 m cells (and the browser's smoothing of
the stretched image) spill color a few hundred meters inland. That is invisible
at half opacity and obvious at full opacity, so rather than rely on the data mask
the website clips the overlay to a real coastline.

This script fetches OpenStreetMap `natural=coastline` ways covering the grid from
the Overpass API, stitches them into continuous chains, closes them into land
polygons and writes them, at OSM's own resolution, as a single SVG path in
`objectBoundingBox` units: coordinates in 0..1 across the
grid rectangle, y increasing south. The path is the unit square followed by the
land polygons, so under `clip-rule="evenodd"` what it selects is exactly the
water. `applyWaterClip` in `js/app.js` hangs it off a hidden <svg> and points the
overlay's `clip-path` at it.

The y axis is Web Mercator, not latitude. Leaflet places the overlay by projecting
the grid's corners and stretching the image between them, so screen position is
linear in mercator y rather than in latitude -- spacing the path linearly in
latitude draws it up to 115 m north of where the basemap puts the same coastline
(the two agree only at the grid's top and bottom edges). The heatmap raster
underneath is still spaced linearly in latitude, but it has no sharp features to
misplace; the clip is the edge the eye lines up against the map.

Coastlines don't move, so this is a one-off: `js/coastline.js` is committed and
`fetch.sh` does not run this. Re-run it only if the forecast grid's bounds change
-- the generated file records the bounds it was built for, but nothing checks them
at runtime, so a stale path would simply be clipped against the wrong rectangle.

The output embeds OpenStreetMap data, (C) OpenStreetMap contributors, licensed
under the ODbL; the website credits OSM in its map attribution.

Quick start
-----------
  uv run -m wavey2.apps.build_coastline  # fetch, then write js/coastline.js
"""

import argparse
import logging
import math
from collections import defaultdict
from pathlib import Path

import requests

LOG = logging.getLogger(Path(__file__).stem)

# Bounds of the NWPS CG3 grid (`metadata.grid` in the waves_*.bin.gz header).
_LAT_MIN, _LAT_MAX = 36.2, 37.0
_LON_MIN, _LON_MAX = -122.2, -121.7

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Seconds to wait for a connection / response before giving up.
_TIMEOUT_SECS = 180

# Overpass wants clients to identify themselves, and overpass-api.de enforces it:
# it answers requests' default `python-requests/x.y` User-Agent with a 406.
_USER_AGENT = "wavey2-build-coastline (+https://github.com/kevinddchen/wavey2)"

# Degrees of padding added to the query bounds, so the coastline is guaranteed to
# run past the edges of the grid rectangle rather than stopping on them. Overpass
# returns whole ways (which overshoot the bounds on their own), so this only has
# to cover the gap to the next node; anything more just ships coastline the
# overlay's own box clips away.
_QUERY_PAD_DEG = 0.01

# Degrees east of the coastline that the mainland polygon is closed at. Only has
# to be strictly east of every coastline point, and outside the grid rectangle.
_CLOSURE_PAD_DEG = 0.05

# Decimal places kept in the emitted path. The unit box spans ~45 km east-west and
# ~89 km north-south, so this rounds coordinates to ~0.5 m across and ~0.9 m up the
# coast: about two pixels at the map's maximum zoom, and the largest error left in
# the path now that nothing is simplified. A sixth decimal costs ~25 KB gzipped.
_PATH_DECIMALS = 5

Point = tuple[float, float]  # (lon, lat)


def fetch_ways(url: str) -> list[list[Point]]:
    """Fetch OSM `natural=coastline` ways covering the (padded) grid bounds."""
    bbox = (
        f"({_LAT_MIN - _QUERY_PAD_DEG},{_LON_MIN - _QUERY_PAD_DEG},"
        f"{_LAT_MAX + _QUERY_PAD_DEG},{_LON_MAX + _QUERY_PAD_DEG})"
    )
    query = f'[out:json][timeout:{_TIMEOUT_SECS}];way["natural"="coastline"]{bbox};out geom;'
    LOG.info(f"Querying {url}: {query}")
    r = requests.post(url, data={"data": query}, headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT_SECS)
    r.raise_for_status()
    ways = [[(p["lon"], p["lat"]) for p in e["geometry"]] for e in r.json()["elements"] if e.get("geometry")]
    LOG.info(f"Read {len(ways)} ways ({sum(len(w) for w in ways)} nodes)")
    return ways


def stitch(ways: list[list[Point]]) -> list[list[Point]]:
    """Join ways that share an endpoint into the longest possible chains.

    OSM splits each coastline into many ways that all run in the same direction
    (land on the left, water on the right), so ways are only ever joined
    head-to-tail. A chain that returns to its own start is a closed ring — an
    island; one that doesn't runs off the edge of the queried area.
    """
    starts: dict[Point, list[int]] = defaultdict(list)
    ends: dict[Point, list[int]] = defaultdict(list)
    for i, way in enumerate(ways):
        starts[way[0]].append(i)
        ends[way[-1]].append(i)

    used = [False] * len(ways)
    chains: list[list[Point]] = []
    for i, way in enumerate(ways):
        if used[i]:
            continue
        used[i] = True
        chain = list(way)
        while chain[0] != chain[-1]:  # extend forward
            nxt = [j for j in starts[chain[-1]] if not used[j]]
            if not nxt:
                break
            used[nxt[0]] = True
            chain += ways[nxt[0]][1:]
        while chain[0] != chain[-1]:  # then backward
            prev = [j for j in ends[chain[0]] if not used[j]]
            if not prev:
                break
            used[prev[0]] = True
            chain = ways[prev[0]][:-1] + chain
        chains.append(chain)

    closed = sum(1 for c in chains if c[0] == c[-1])
    LOG.info(f"Stitched into {len(chains)} chains ({closed} closed, {len(chains) - closed} open)")
    return chains


def close_mainland(chain: list[Point]) -> list[Point]:
    """Close the open mainland chain into a polygon by running around its land side.

    The mainland coastline crosses the grid from north to south with the ocean to
    the west, so both ends leave the grid rectangle through its top and bottom
    edges. Joining them east of every coastline point — and of the rectangle
    itself, so the seam can never fall inside the mask — closes the polygon over
    land, outside the rectangle, where it can't cut into the water.
    """
    north, south = chain[0], chain[-1]
    if not (north[1] > _LAT_MAX and south[1] < _LAT_MIN):
        raise ValueError(
            f"mainland chain does not span the grid: ends at {north} and {south}, "
            f"expected latitudes outside [{_LAT_MIN}, {_LAT_MAX}]"
        )
    east = max(*(lon for lon, _ in chain), _LON_MAX) + _CLOSURE_PAD_DEG
    return chain + [(east, south[1]), (east, north[1])]


def land_polygons(chains: list[list[Point]]) -> list[list[Point]]:
    """Turn stitched chains into closed land polygons, at OSM's own resolution.

    Exactly one chain is expected to be open (the mainland, closed by
    `close_mainland`); anything else open means the coastline data changed shape
    and the assumptions here need revisiting. The rest are islands — every one of
    them, down to individual rocks, is kept.
    """
    open_chains = [c for c in chains if c[0] != c[-1]]
    if len(open_chains) != 1:
        raise ValueError(f"expected exactly 1 open coastline chain, got {len(open_chains)}")

    islands = [c for c in chains if c[0] == c[-1]]
    LOG.info(f"Closed the mainland and kept {len(islands)} islands")
    return [close_mainland(open_chains[0])] + islands


def _fmt(value: float) -> str:
    """Format a unit-box coordinate as compactly as SVG allows (`0.25` -> `.25`)."""
    s = f"{value:.{_PATH_DECIMALS}f}".rstrip("0").rstrip(".")
    s = s.replace("-0.", "-.") if s.startswith("-0.") else s.removeprefix("0") if s.startswith("0.") else s
    return s or "0"


def _mercator_y(lat: float) -> float:
    """Web Mercator northing (in earth radii) for a latitude, the way Leaflet projects it."""
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def to_path(polygons: list[list[Point]]) -> str:
    """Render land polygons as an SVG path in `objectBoundingBox` units.

    x is linear in longitude and y in Web Mercator northing, which is how Leaflet
    stretches the overlay across the grid rectangle. The unit square comes first,
    so `clip-rule="evenodd"` selects the water: the square minus the land, plus
    anything (an island's own lagoon) nested inside it.
    """
    y_min, y_max = _mercator_y(_LAT_MIN), _mercator_y(_LAT_MAX)
    parts = ["M0 0H1V1H0Z"]
    for ring in polygons:
        coords = []
        for lon, lat in ring:
            x = (lon - _LON_MIN) / (_LON_MAX - _LON_MIN)
            y = (y_max - _mercator_y(lat)) / (y_max - y_min)
            coords.append(f"{_fmt(x)} {_fmt(y)}")
        parts.append(f"M{coords[0]}L{' '.join(coords[1:])}Z")
    return "".join(parts)


def build_coastline(out_path: Path) -> None:
    polygons = land_polygons(stitch(fetch_ways(_OVERPASS_URL)))
    LOG.info(f"{sum(len(r) for r in polygons)} points in {len(polygons)} polygons")

    path = to_path(polygons)
    grid = f"lat_min: {_LAT_MIN}, lat_max: {_LAT_MAX}, lon_min: {_LON_MIN}, lon_max: {_LON_MAX}"
    out_path.write_text(
        "/* exported COASTLINE */\n"
        "// Generated by wavey2.apps.build_coastline -- do not edit by hand.\n"
        "// Coastline from OpenStreetMap, (C) OpenStreetMap contributors, ODbL.\n"
        "//\n"
        "// `path` is the water mask the wave heatmap is clipped to: an SVG path in\n"
        "// objectBoundingBox units across `grid` -- x linear in longitude, y linear in\n"
        "// Web Mercator northing and increasing south, matching how Leaflet stretches the\n"
        "// overlay -- holding the unit square followed by the land polygons, so\n"
        "// `clip-rule=evenodd` selects the water. `grid` records the forecast grid this\n"
        "// was built for -- it is not read at runtime, so rebuild if those bounds change.\n"
        "const COASTLINE = {\n"
        f"    grid: {{ {grid} }},\n"
        f'    path: "{path}",\n'
        "};\n"
    )
    LOG.info(f"Wrote '{out_path}' ({len(path) / 1024:.0f} KiB path)")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build the coastline water mask the wave heatmap is clipped to.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # fmt: off
    ap.add_argument(
        "--out", "-o", type=Path, default=Path("./js/coastline.js"), help="Output .js file to write",
    )
    # fmt: on
    args = ap.parse_args()

    build_coastline(out_path=args.out)


if __name__ == "__main__":
    from wavey2.logging import setup_logging

    setup_logging()
    main()
