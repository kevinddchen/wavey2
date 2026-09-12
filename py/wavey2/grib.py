import os
from pathlib import Path

_GRIB_MAGIC = b"GRIB"
_GRIB_END = b"7777"


def check_grib2(path: Path) -> bool:
    """
    Check that a file has GRIB2 framing: every GRIB2 file starts with "GRIB" and
    ends with "7777".

    Args:
        path: File to check.

    Returns:
        Whether `path` is a GRIB2 file.
    """

    size = path.stat().st_size
    if size < len(_GRIB_MAGIC) + len(_GRIB_END):
        # file is too small
        return False

    with open(path, "rb") as f:
        head = f.read(len(_GRIB_MAGIC))
        f.seek(-len(_GRIB_END), os.SEEK_END)
        tail = f.read(len(_GRIB_END))

    return head == _GRIB_MAGIC and tail == _GRIB_END
