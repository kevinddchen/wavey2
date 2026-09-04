"""
The JSON header carried by every `waves_<run_id>.bin.gz` file.

`grib2bin` builds a `Header` and serializes it into the file; `build_index` reads
those bytes back and validates them into the same model, so writer and reader
can't drift. `decodeBinary` in `js/app.js` is a third reader of this layout and
has no schema of its own — mirror any change there, and bump `HEADER_VERSION`
whenever the binary layout changes.

The surrounding file layout is documented in `wavey2.apps.grib2bin`.
"""

from typing import Literal

from pydantic import BaseModel

# Bump when the binary layout changes; `decodeBinary` in `js/app.js` reads it.
HEADER_VERSION = 1

# Storage type of a quantized array. Each reserves its most-extreme value as the
# sentinel; see `_DTYPE_INFO` in `wavey2.apps.grib2bin` for the usable ranges.
DType = Literal["uint8", "int8", "int16"]

# Applied to the real value on encode; the decoder applies the inverse
# (identity / square).
Transform = Literal["linear", "sqrt"]


class Grid(BaseModel):
    """Bounds of the (regular lat/lon) forecast grid. `nx * ny == Header.ncells`."""

    nx: int
    ny: int
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


class Units(BaseModel):
    """NOAA's native units — the website applies its own display scaling."""

    wave_height: str
    wave_dir: str
    wave_period: str
    water_level: str


class Metadata(BaseModel):
    """Where the forecast came from, and what its axes are."""

    source: str
    # GRIB2 analysis time, `YYYY-MM-DDTHH:MM:SSZ`.
    forecast_time: str
    # One `YYYY-MM-DDTHH:MM:SSZ` per timestep; length is `Header.nt`.
    times: list[str]
    grid: Grid
    units: Units


class Variable(BaseModel):
    """
    One quantized array in the payload, decoded as:

        real = inverse_transform(int_value / scale)   (when int_value != sentinel)
    """

    name: str
    dtype: DType
    scale: float
    # The encoded value meaning "no data" (e.g. land cells for wave fields).
    sentinel: int
    transform: Transform = "linear"


class Header(BaseModel):
    """The JSON header of a `waves_<run_id>.bin.gz` file."""

    version: int = HEADER_VERSION
    metadata: Metadata
    # Number of grid cells, `= metadata.grid.nx * metadata.grid.ny`.
    ncells: int
    # Number of timesteps, `= len(metadata.times)`.
    nt: int
    # One entry per binary array, in the order they appear in the payload.
    variables: list[Variable]
