import argparse
import logging
import os
import re
from collections.abc import Iterator
from pathlib import Path

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger(__name__)

_BASE_URL = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod"
_MTR = "mtr"
_CG3 = "CG3"

_CHUNK_SIZE = 8192


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
    LOG.info(f"Found NWFS forecasts: {dates}")

    for date in dates:
        LOG.info(f"Looking in '{date}'...")
        try:
            times = _list_times(date)
        except requests.HTTPError:
            continue
        for time in times:
            if _check_time(date=date, time=time):
                yield _get_url(date=date, time=time)


def get_most_recent_forecast() -> str:
    """
    Get most recent NWFS forecast data for Monterey bay.

    Returns:
        URL to the GRIB file.

    Raises:
        HTTPError: If accessing website returns error.
    """

    url = next(_iter_forecast_urls(), None)
    assert url is not None, "Unexpected: could not find any forecasts for Monterey bay."
    LOG.info(f"Found most recent forecast: {url}")
    return url


def get_all_available_forecasts() -> list[str]:
    """
    Get all available Monterey bay "CG3" forecasts retained on the server.

    Unlike `get_all_forecasts`, this is not restricted to a single run hour: it
    returns every (date, time) run that has a CG3 forecast, newest first.

    Returns:
        List of URLs to GRIB files, newest first.

    Raises:
        HTTPError: If accessing the index website returns an error.
    """

    return list(_iter_forecast_urls())


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

    r = requests.get(url)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    hrefs: list[str] = [link["href"] for link in soup.find_all("a", href=True)]  # type: ignore[misc]

    if regex is not None:
        hrefs = list(filter(lambda x: re.match(regex, x), hrefs))

    return hrefs


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
    r = requests.get(url)
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


def get_all_forecasts(time: str = "06") -> list[str]:
    """
    Get all NWFS forecast data for Monterey bay.

    Args:
        time: A string like "HH/".

    Returns:
        List of URLS to GRIB files.

    Raises:
        HTTPError: If accessing website returns error.
    """

    # 1. List dates with forecasts
    dates = _list_dates()
    LOG.info(f"Found NWFS forecasts: {dates}")

    # 2. For each date, check for Monterey bay forecast on the given time
    good_dates = [date for date in dates if _check_time(date=date, time=time)]

    return [_get_url(date=date, time=time) for date in good_dates]


def download_forecast(url: str, dir: Path | None = None, path: Path | None = None) -> Path:
    """
    Download NWFS forecast data to disk.

    Args:
        url: URL to the GRIB file.
        dir: Directory to save the file in. If none, will download to the
            current directory. Ignored if `path` is set.
        path: Full path to save the file to. Takes precedence over `dir`.

    Returns:
        Path to the GRIB file.

    Raises:
        HTTPError: If error encountered during download.
    """

    if path is not None:
        file_path = path
    else:
        if dir is None:
            dir = Path(".")
        file_path = dir / os.path.basename(url)
    if file_path.exists():
        LOG.info(f"'{file_path}' already exists. Skipping download")
        return file_path

    LOG.info(f"Downloading '{url}' to '{file_path}'")
    r = requests.get(url, stream=True)
    r.raise_for_status()

    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "wb") as file:
        for chunk in r.iter_content(chunk_size=_CHUNK_SIZE):
            file.write(chunk)

    return file_path


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Download Monterey Bay NWPS GRIB2 forecast(s).")
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Download every available forecast run, printing one path per line",
    )
    parser.add_argument(
        "--dir",
        "-d",
        type=Path,
        default=None,
        help="Directory to save the file (default: cwd)",
    )
    args = parser.parse_args()

    if args.all:
        urls = get_all_available_forecasts()
        LOG.info(f"Found {len(urls)} forecast(s)")
    else:
        urls = [get_most_recent_forecast()]

    for url in urls:
        try:
            download_forecast(url, dir=args.dir)
        except requests.HTTPError as e:
            LOG.warning(f"Failed to download '{url}': {e}")
            continue


if __name__ == "__main__":
    main()
