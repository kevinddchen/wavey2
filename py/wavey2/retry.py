"""
Retrying NOAA requests that come back rate-limited.

NOAA rate-limits by IP and answers a refused request with an HTML notice rather than an
error status, so a "successful" response is not necessarily the data that was asked for.
GitHub-hosted runners share egress IPs, so a scheduled build can be refused because of
unrelated jobs — waiting out the per-minute window is usually enough.
"""

import logging
import operator
from pathlib import Path
from typing import Any, Callable

import tenacity

LOG = logging.getLogger(Path(__file__).stem)

# Seconds to wait for a connection / between received chunks before giving up.
TIMEOUT_SECS = 30

# Retry schedule for requests NOAA refuses for rate-limit reasons. `wait_exponential`
# waits `multiplier * exp_base ** (attempt - 1)`, so these give 5s, then 15s, then 45s.
# The quota is per-minute, so waiting about that long clears a burst.
MAX_TRIES = 4
WAIT_MULTIPLIER = 5
WAIT_EXP_BASE = 3

# Markers in NOAA's rate-limit page. It arrives under a 200, so its body is the only
# reliable way to tell it apart from the data that was asked for.
_RATE_LIMIT_MARKERS = ("over rate limit", "abusive-user-block")

# How much of a body to search for those markers. The page is under a kilobyte and names
# itself in the first few hundred bytes.
_MARKER_BYTES = 2 * 1024


def _log_retry(state: tenacity.RetryCallState) -> None:
    """
    Log an attempt that is about to be retried.

    Args:
        state: What `tenacity` records about the call.
    """

    wait = state.next_action.sleep if state.next_action else 0.0
    LOG.warning(
        f"Request did not return the data asked for. "
        f"Retrying in {wait:.0f}s (try {state.attempt_number} of {MAX_TRIES})"
    )


def _log_giveup(state: tenacity.RetryCallState) -> Any:
    """
    Log a call that has run out of retries, and hand back the result it ended on.

    This overrides the default `tenacity` behavior of raising `RetryError` once the
    stop condition fires.

    Args:
        state: What `tenacity` records about the call.

    Returns:
        The result of the final attempt.
    """

    LOG.error(f"Request still did not return the data asked for after {state.attempt_number} tries")
    assert state.outcome is not None, "the stop condition only fires after an attempt"
    return state.outcome.result()


def retry[T: Callable[..., Any]](predicate: Callable[[Any], bool] = operator.not_) -> Callable[[T], T]:
    """
    Decorate a function so it is retried while `predicate` holds of its return value.

    Retries follow the schedule above, and each one is logged. Once the tries run out the
    last result is returned as it is, so a decorated function reports the failure through
    its return value rather than by raising.

    Args:
        predicate: Called with the return value; a true result means try again. Defaults
            to retrying while the return value is falsy.

    Returns:
        The decorator.
    """

    return tenacity.retry(
        retry=tenacity.retry_if_result(predicate),
        stop=tenacity.stop_after_attempt(MAX_TRIES),
        wait=tenacity.wait_exponential(multiplier=WAIT_MULTIPLIER, exp_base=WAIT_EXP_BASE),
        before_sleep=_log_retry,
        retry_error_callback=_log_giveup,
    )


def is_rate_limit_page(path: Path) -> bool:
    """
    Check whether a downloaded file is NOAA's rate-limit notice rather than data.

    For responses that were streamed to disk, where the body could not be examined as it
    arrived without consuming it from the caller.

    Args:
        path: File to check.

    Returns:
        Whether the file holds a rate-limit notice.
    """

    with open(path, "rb") as f:
        head = f.read(_MARKER_BYTES)

    head_lowered = head.decode("utf-8", errors="ignore").lower()
    return any(marker in head_lowered for marker in _RATE_LIMIT_MARKERS)
