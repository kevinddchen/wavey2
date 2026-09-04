import argparse
import logging
import os
import re
from pathlib import Path
from typing import Iterator

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger(Path(__file__).stem)

_BASE_URL = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod"
_MTR = "mtr"
_CG3 = "CG3"

_CHUNK_SIZE = 8192

# Seconds to wait for a connection / between received chunks before giving up.
_TIMEOUT_SECS = 30

# Framing of a GRIB2 file: it starts with "GRIB" and ends with "7777".
_GRIB_MAGIC = b"GRIB"
_GRIB_END = b"7777"


def get_most_recent_forecast() -> str:
    """
    Get most recent NWFS forecast data for Monterey bay.

    Returns:
        URL to the GRIB file.

    Raises:
        HTTPError: If accessing website returns error.
        RuntimeError: If no forecasts were found.
    """

    url = next(_iter_forecast_urls(), None)
    if url is None:
        raise RuntimeError("Could not find any forecasts for Monterey bay.")
    return url


def get_all_available_forecasts() -> list[str]:
    """
    Get all available NWFS forecast data for Monterey bay.

    Returns:
        List of URLs to GRIB files, newest first.

    Raises:
        HTTPError: If accessing the index website returns an error.
    """

    return list(_iter_forecast_urls())


def _iter_forecast_urls() -> Iterator[str]:
    """
    Yield URLs for every available Monterey bay "CG3" forecast, newest first.

    Walks each forecast date (newest first) and, within each, each run hour
    (newest first), yielding a URL for every run that has a CG3 forecast. Lazy,
    so callers that only need the most recent can stop after the first item.

    Raises:
        HTTPError: If accessing the index website returns an error.
    """

    dates = _list_dates()
    LOG.info(f"Found forecast dates: {dates}")

    for date in dates:
        LOG.info(f"Looking in '{date}'...")
        try:
            times = _list_times(date)
        except requests.HTTPError:
            continue  # no forecasts on the given date; try next date
        for time in times:
            if _check_time(date=date, time=time):
                yield _get_url(date=date, time=time)


def _list_dates() -> list[str]:
    """
    List dates with Western Region (wr) forecasts.

    Returns:
        List of strings like "wr.YYYYMMDD/"; sorted (most recent first).

    Raises:
        HTTPError: If accessing website returns error.
    """

    url = _BASE_URL
    dates = _get_hrefs(url, r"wr\.\d{8}")  # hrefs look like "wr.YYYYMMDD/"
    return sorted(dates, reverse=True)


def _list_times(date: str) -> list[str]:
    """
    List times with forecasts for Monterey bay on the given date.

    Args:
        date: A string like "wr.YYYYMMDD/".

    Returns:
        List of strings like "HH/"; sorted (most recent first).

    Raises:
        HTTPError: If no forecasts for Monterey on the given date.
    """

    url = os.path.join(_BASE_URL, date, _MTR)
    times = _get_hrefs(url, r"\d{2}")  # hrefs look like "HH/"
    return sorted(times, reverse=True)


def _check_time(date: str, time: str) -> bool:
    """
    Check if "CG3" forecast is available for the given time.

    Args:
        date: A string like "wr.YYYYMMDD/".
        time: A string like "HH/".

    Returns:
        True if a "CG3" forecast is available, else False.
    """

    url = os.path.join(_BASE_URL, date, _MTR, time, _CG3)
    r = requests.get(url, timeout=_TIMEOUT_SECS)
    return r.ok


def _get_url(date: str, time: str) -> str:
    """
    Given date and time, get URL to the GRIB file.

    Args:
        date: A string like "wr.YYYYMMDD/".
        time: A string like "HH/".

    Returns:
        URL to the GRIB file.
    """

    date_match = re.search(r"\d{8}", date)
    assert date_match, f"Unexpected date: {date}"
    time_match = re.search(r"\d{2}", time)
    assert time_match, f"Unexpected time: {time}"

    yyyymmdd = date_match.group(0)
    hh = time_match.group(0)
    filename = f"{_MTR}_nwps_{_CG3}_{yyyymmdd}_{hh}00.grib2"

    return os.path.join(_BASE_URL, date, _MTR, time, _CG3, filename)


def _get_hrefs(url: str, regex: str | None = None) -> list[str]:
    """
    Navigate to URL and return all hrefs on the webpage.

    Args:
        url: URL of webpage.
        regex: If provided, will only return matching hrefs.

    Returns:
        List of strings.

    Raises:
        HTTPError: If accessing URL returns error.
    """

    r = requests.get(url, timeout=_TIMEOUT_SECS)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    hrefs: list[str] = [link["href"] for link in soup.find_all("a", href=True)]  # type: ignore[misc]

    if regex is not None:
        hrefs = list(filter(lambda x: re.match(regex, x), hrefs))

    return hrefs


def download_forecast(url: str, dir: Path) -> Path:
    """
    Download NWFS forecast data to disk.

    Args:
        url: URL to the GRIB file.
        dir: Directory to save the file in.

    Returns:
        Path to the GRIB file.

    Raises:
        HTTPError: If error encountered during download.
    """

    file_path = dir / os.path.basename(url)
    if file_path.exists():
        LOG.warning(f"'{file_path}' already exists. Skipping download")
        return file_path

    r = requests.get(url, stream=True, timeout=_TIMEOUT_SECS)
    r.raise_for_status()

    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "wb") as file:
        for chunk in r.iter_content(chunk_size=_CHUNK_SIZE):
            file.write(chunk)

    size_mb = file_path.stat().st_size / 1e6
    LOG.info(f"Downloaded '{url}' to '{file_path}' ({size_mb:.1f} MB)")
    return file_path


def check_grib2(path: Path) -> None:
    """
    Check that a file has GRIB2 framing: every GRIB2 file starts with "GRIB" and
    ends with "7777".

    Args:
        path: File to check.

    Raises:
        RuntimeError: If the file is not a GRIB2 file.
    """

    size = path.stat().st_size
    if size >= len(_GRIB_MAGIC) + len(_GRIB_END):
        with open(path, "rb") as f:
            head = f.read(len(_GRIB_MAGIC))
            f.seek(-len(_GRIB_END), os.SEEK_END)
            tail = f.read(len(_GRIB_END))
        if head == _GRIB_MAGIC and tail == _GRIB_END:
            return
    else:
        head = tail = b""
    raise RuntimeError(f"'{path.name}' is not a GRIB2 file (starts {head!r}, ends {tail!r})")


def main() -> None:
    # =========================================================================

    ap = argparse.ArgumentParser(
        description="Download Monterey Bay NWPS GRIB2 forecasts.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Download every available forecast",
    )
    ap.add_argument(
        "--out-dir",
        "-o",
        type=Path,
        default=Path("./gribs/"),
        help="Output directory to save the .grib2 files",
    )
    args = ap.parse_args()

    # =========================================================================

    if args.all:
        urls = get_all_available_forecasts()
        LOG.info(f"Found {len(urls)} forecast(s)")
    else:
        urls = [get_most_recent_forecast()]
        LOG.info(f"Found most recent forecast: {urls[0]}")

    downloaded = 0
    for url in urls:
        try:
            file_path = download_forecast(url, dir=args.out_dir)
        except requests.HTTPError as e:
            LOG.warning(f"Failed to download '{url}': {e}")
            continue

        check_grib2(file_path)
        downloaded += 1

    if downloaded == 0:
        raise RuntimeError(f"Failed to download any of the {len(urls)} available forecast(s).")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] [%(asctime)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    main()
