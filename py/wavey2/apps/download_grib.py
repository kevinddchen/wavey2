import logging
import os
import re
from pathlib import Path
from typing import Iterator

import requests
import tyro
from bs4 import BeautifulSoup

from wavey2.grib import check_grib2
from wavey2.logging import setup_logging
from wavey2.retry import TIMEOUT_SECS, is_rate_limit_page, retry

LOG = logging.getLogger(Path(__file__).stem)

_BASE_URL = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod"
_MTR = "mtr"
_CG3 = "CG3"


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
    r = requests.get(url, timeout=TIMEOUT_SECS)
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

    r = requests.get(url, timeout=TIMEOUT_SECS)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    hrefs: list[str] = [link["href"] for link in soup.find_all("a", href=True)]  # ty: ignore[invalid-assignment]

    if regex is not None:
        hrefs = list(filter(lambda x: re.match(regex, x), hrefs))

    return hrefs


def download_forecast(url: str, dir: Path, chunk_size: int | None = 8 * 1024) -> Path:
    """
    Download NWFS forecast data to disk.

    Args:
        url: URL to the GRIB file.
        dir: Directory to save the file in.
        chunk_size: Download chunk size, in bytes.

    Returns:
        Path to the GRIB file.

    Raises:
        HTTPError: If error encountered during download.
        RuntimeError: If the response is not a GRIB2 file.
    """

    filename = os.path.basename(url)
    file_path = dir / filename
    if file_path.exists():
        LOG.info(f"'{file_path}' already downloaded. Skipping")
        return file_path

    LOG.info(f"Downloading '{url}' to '{file_path}'...")
    dir.mkdir(parents=True, exist_ok=True)

    tmp_path = dir / f"{filename}.part"
    try:
        _download_grib2(url, tmp_path, chunk_size=chunk_size)
        if not check_grib2(tmp_path):
            LOG.error(
                f"'{filename}' is not a GRIB2 file ({tmp_path.stat().st_size} bytes). Contents:\n{_preview(tmp_path)}"
            )
            raise RuntimeError(f"'{filename}' is not a GRIB2 file.")
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    tmp_path.replace(file_path)

    size_mb = file_path.stat().st_size / 1e6
    LOG.info(f"... done ({size_mb:.1f} MB)")
    return file_path


@retry(is_rate_limit_page)
def _download_grib2(url: str, path: Path, chunk_size: int | None) -> Path:
    """
    Download `url` to `path`, retrying only while NOAA answers with its rate-limit notice.

    That notice arrives under a 200, so `raise_for_status` lets it through; the body of a
    streamed response cannot be examined without consuming it, so the predicate reads the
    written file back instead. Anything else that is not a GRIB2 file is left to the
    caller to report rather than retried: NOAA's published files do not change, so a
    second attempt would only fetch the same bytes again. A transfer cut short does not
    reach the predicate at all — NOAA sends a `Content-Length`, so a short read raises.

    Args:
        url: URL to the GRIB file.
        path: File to write the response to. Overwritten on each attempt.
        chunk_size: Download chunk size, in bytes.

    Returns:
        `path`, which the retry predicate reads to decide whether to try again.

    Raises:
        HTTPError: If a request returns an error status.
    """

    r = requests.get(url, stream=True, timeout=TIMEOUT_SECS)
    r.raise_for_status()

    with open(path, "wb") as file:
        for chunk in r.iter_content(chunk_size=chunk_size):
            file.write(chunk)

    return path


def _preview(path: Path, preview_bytes: int = 2 * 1024) -> str:
    """
    Read the start of a file as text, for logging what a bad download contains.

    Args:
        path: File to read.
        preview_bytes: How much of a file that failed the GRIB2 check to log.

    Returns:
        The first `preview_bytes` bytes decoded as UTF-8 — bytes that are not
        valid UTF-8 are escaped rather than dropped, so binary junk is still
        readable — with a trailing "..." if the file is longer than that.
    """

    with open(path, "rb") as f:
        data = f.read(preview_bytes + 1)

    text = data[:preview_bytes].decode("utf-8", errors="backslashreplace")
    return f"{text}..." if len(data) > preview_bytes else text


def prune_forecasts(dir: Path, keep: int) -> None:
    """
    Delete all but the `keep` most recent forecasts in a download directory.

    The directory is reused between builds — the deploy workflow caches it, so most of
    what NOAA serves has already been downloaded by an earlier build — which means
    something has to bound it. NOAA keeps about ten runs on the server at a time, so
    keeping that many tracks its retention window: what gets dropped here is what has
    already aged off the server.

    Run filenames sort chronologically, so the newest are simply the last by name.

    Args:
        dir: Directory of .grib2 files.
        keep: How many forecasts to keep.

    Raises:
        ValueError: If `keep` is not positive.
    """

    if keep < 1:
        raise ValueError(f"Must keep at least one forecast, got {keep}.")

    # A ".part" file is left behind only when a download is killed outright, since the
    # download itself cleans up after anything it can catch. That used to be wiped with
    # the rest of the directory; now that the directory outlives the build, an
    # interrupted download would otherwise be cached forever.
    for path in dir.glob("*.grib2.part"):
        LOG.warning(f"Removing leftover partial download '{path.name}'")
        path.unlink(missing_ok=True)

    stale = sorted(dir.glob("*.grib2"), reverse=True)[keep:]
    for path in stale:
        path.unlink(missing_ok=True)

    if stale:
        LOG.info(f"Pruned {len(stale)} forecast(s) older than the newest {keep}")


def main(
    download_all: bool = True,
    out_dir: Path = Path("./gribs/"),
    keep: int | None = None,
) -> None:
    """
    Download Monterey Bay NWPS GRIB2 forecasts.

    Args:
        download_all: Download every available forecast. Otherwise, downloads
            just the most recent one.
        out_dir: Output directory to save the .grib2 files. Files already there are
            kept rather than downloaded again.
        keep: How many forecasts to leave in `out_dir`, newest first. Older ones are
            deleted once the downloads are done. `None` means do not prune.
    """

    if download_all:
        urls = get_all_available_forecasts()
        LOG.info(f"Found {len(urls)} forecast(s)")
    else:
        urls = [get_most_recent_forecast()]
        LOG.info(f"Found most recent forecast: {urls[0]}")

    downloaded = 0
    for url in urls:
        try:
            download_forecast(url, dir=out_dir)
        except requests.HTTPError as e:
            LOG.warning(f"Failed to download '{url}': {e}")
            continue

        downloaded += 1

    if keep is not None:
        prune_forecasts(out_dir, keep=keep)

    if downloaded == 0:
        raise RuntimeError(f"Failed to download any of the {len(urls)} available forecast(s).")


if __name__ == "__main__":
    setup_logging()
    tyro.cli(main)
