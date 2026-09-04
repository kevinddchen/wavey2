import logging
import time

_FORMAT = "[%(levelname)s] [%(asctime)s] %(name)s: %(message)s"
# ISO 8601, with the literal `Z` marking the UTC offset that `_UTCFormatter` guarantees.
_DATEFMT = "%Y-%m-%dT%H:%M:%SZ"


class _UTCFormatter(logging.Formatter):
    """
    A `logging.Formatter` that renders `%(asctime)s` in UTC rather than local time.

    `converter` is what `formatTime` runs the record's epoch timestamp through;
    the default is `time.localtime`.
    """

    @staticmethod
    def converter(timestamp: float | None = None) -> time.struct_time:
        return time.gmtime(timestamp)


def setup_logging(level: int | str | None = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_UTCFormatter(_FORMAT, _DATEFMT))
    logging.basicConfig(level=level, handlers=[handler])
