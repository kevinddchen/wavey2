"""
Sync downloaded NWPS GRIB2 files to an S3 bucket, for long-term archival.

NOAA retains only about four days of Monterey Bay CG3 runs (two per day, 00Z and
12Z), so studying forecast skill — how the forecast for a given time changes as
its lead time shortens — means keeping runs as they are published. The deploy
workflow already downloads every available run into `gribs/` on each build, so
this script does no downloading of its own: it uploads whatever is in a local
directory and not yet in the bucket.

Objects are keyed by run time, so a month can be listed under one prefix and the
whole archive sorts chronologically:

    <prefix>/YYYY/MM/mtr_nwps_CG3_YYYYMMDD_HH00.grib2.gz

Files are gzipped before upload. GRIB2's own packing leaves plenty of redundancy,
so this takes a run from ~28 MB to ~11.5 MB — a bit under half the storage bill,
losslessly, for a fraction of a second of CPU.

Uploads set S3's native SHA-256 checksum (so a corrupted transfer is rejected
rather than stored) and record two digests in user metadata: `x-amz-meta-sha256`
for the stored gzip object, and `x-amz-meta-sha256-uncompressed` for the GRIB2
inside it, which is what proves the data itself survived. Files are also checked
for GRIB2 framing before upload, so a truncated download does not become a
permanent bad record.

The bucket holds nothing but the compressed GRIB2 files themselves; which runs are
archived is whatever a listing of the prefix says it is.

Because every invocation re-checks the whole local directory against the bucket,
and the deploy workflow refreshes that directory from NOAA's full retention
window, a missed or failed upload is retried by later builds: the archive
self-heals as long as no more than about four days pass between successful runs.

Credentials come from the usual boto3 chain; in CI that is the OIDC role assumed
by the workflow. `--endpoint-url` points the same script at any S3-compatible
store (R2, MinIO).

Quick start
-----------
  uv run --group archive -m wavey2.apps.sync_gribs --bucket my-bucket --dry-run
  uv run --group archive -m wavey2.apps.sync_gribs --bucket my-bucket
"""

import argparse
import gzip
import hashlib
import logging
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import BotoCoreError, ClientError

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

LOG = logging.getLogger(Path(__file__).stem)

# Filenames NOAA serves, e.g. "mtr_nwps_CG3_20260831_1200.grib2". The optional
# ".gz" lets this match archived object keys too, which are gzipped.
_GRIB_RE = re.compile(r"^mtr_nwps_CG3_(\d{4})(\d{2})(\d{2})_(\d{4})\.grib2(?:\.gz)?$")

# GRIB2 packing is not itself compressed, so gzip still takes roughly 28 MB down
# to 11.5 MB. Level 9 buys another 0.2% for 3x the CPU, so the default is fine.
_GZIP_LEVEL = 6

# Every GRIB2 message starts with "GRIB" and ends with "7777"; a file that fails
# this was truncated mid-download and must not be archived as if it were good.
_GRIB_MAGIC = b"GRIB"
_GRIB_END = b"7777"


def parse_filename(filename: str) -> tuple[str, str, str]:
    """
    Split a NOAA GRIB2 filename into the parts the object key is built from.

    Args:
        filename: Basename like "mtr_nwps_CG3_20260831_1200.grib2".

    Returns:
        Tuple of (run_id, year, month), e.g. ("20260831_1200", "2026", "08").

    Raises:
        ValueError: If the filename is not a Monterey Bay CG3 GRIB2 file.
    """

    m = _GRIB_RE.match(filename)
    if not m:
        raise ValueError(f"not an NWPS CG3 GRIB2 filename: {filename!r}")
    yyyy, mm, dd, hhmm = m.groups()
    return f"{yyyy}{mm}{dd}_{hhmm}", yyyy, mm


def object_key(prefix: str, filename: str) -> str:
    """Build the S3 key for a GRIB2 file: "<prefix>/YYYY/MM/<filename>.gz"."""
    _, yyyy, mm = parse_filename(filename)
    return f"{prefix}/{yyyy}/{mm}/{filename}.gz"


def check_grib2(path: Path) -> None:
    """
    Check that a file is a complete GRIB2 file.

    Args:
        path: Local file to check.

    Raises:
        ValueError: If the GRIB2 framing is missing, i.e. the file is truncated
            or is not a GRIB2 file at all.
    """

    size = path.stat().st_size
    if size < len(_GRIB_MAGIC) + len(_GRIB_END):
        raise ValueError(f"{path.name} is too small to be a GRIB2 file ({size} bytes)")
    with open(path, "rb") as f:
        head = f.read(len(_GRIB_MAGIC))
        f.seek(-len(_GRIB_END), 2)
        tail = f.read(len(_GRIB_END))
    if head != _GRIB_MAGIC or tail != _GRIB_END:
        raise ValueError(f"{path.name} is not a complete GRIB2 file (head {head!r}, tail {tail!r})")


def list_archived(s3: "S3Client", bucket: str, prefix: str) -> set[str]:
    """
    List the runs already in the bucket.

    Args:
        s3: S3 client.
        bucket: Bucket name.
        prefix: Key prefix the archive lives under (no trailing slash).

    Returns:
        Set of archived run ids. Keys that don't look like an NWPS GRIB2 file
        (anything else sharing the prefix) are ignored.

    Raises:
        ClientError: If the bucket cannot be listed.
    """

    archived: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/"):
        for obj in page.get("Contents", []):
            try:
                run_id, _, _ = parse_filename(obj["Key"].rsplit("/", 1)[-1])
            except ValueError:
                continue
            archived.add(run_id)
    return archived


def upload(s3: "S3Client", path: Path, bucket: str, key: str) -> tuple[str, int]:
    """
    Gzip one GRIB2 file and upload it, with checksums for later verification.

    The gzip stream is written with no embedded filename and a zero mtime, so
    compressing the same GRIB2 file twice gives byte-identical output — a re-upload
    can be recognised as a no-op rather than looking like new data.

    Args:
        s3: S3 client.
        path: Local (uncompressed) GRIB2 file.
        bucket: Bucket name.
        key: Destination key, ending in ".gz".

    Returns:
        Tuple of (SHA-256 hex digest of the uncompressed GRIB2, compressed size
        in bytes).

    Raises:
        ClientError: If the upload is rejected (including a checksum mismatch).
    """

    with open(path, "rb") as f:
        raw_digest = hashlib.file_digest(f, "sha256").hexdigest()

    with tempfile.NamedTemporaryFile(suffix=".gz") as tmp:
        with open(path, "rb") as src:
            with gzip.GzipFile(filename="", mode="wb", fileobj=tmp, compresslevel=_GZIP_LEVEL, mtime=0) as gz:
                shutil.copyfileobj(src, gz)
        tmp.flush()
        size = os.fstat(tmp.fileno()).st_size

        tmp.seek(0)
        digest = hashlib.file_digest(tmp, "sha256").hexdigest()

        tmp.seek(0)
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=tmp,
            ContentType="application/gzip",
            ChecksumAlgorithm="SHA256",
            Metadata={
                # `sha256` is of the stored (compressed) object; the uncompressed
                # digest is what proves the GRIB2 itself survived the round trip,
                # independently of how it was compressed.
                "sha256": digest,
                "sha256-uncompressed": raw_digest,
                "archived-at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
    return raw_digest, size


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Sync downloaded NWPS GRIB2 files to S3.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # fmt: off
    ap.add_argument(
        "--dir", "-d", type=Path, default=Path("./gribs/"), help="Directory of .grib2 files to sync",
    )
    ap.add_argument(
        "--bucket", "-b", required=True, help="Destination S3 bucket",
    )
    ap.add_argument(
        "--prefix", "-p", default="nwps/mtr/CG3", help="Key prefix to archive under",
    )
    ap.add_argument(
        "--endpoint-url", default=None, help="S3 endpoint, for S3-compatible stores (R2, MinIO)",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="Report what would be uploaded, without uploading",
    )
    # fmt: on
    args = ap.parse_args()

    grib_dir: Path = args.dir
    prefix: str = args.prefix.strip("/")

    local: dict[str, Path] = {}
    for path in sorted(grib_dir.glob("*.grib2")):
        try:
            run_id, _, _ = parse_filename(path.name)
        except ValueError as e:
            LOG.warning(f"Skipping {e}")
            continue
        local[run_id] = path
    if not local:
        raise FileNotFoundError(f"no mtr_nwps_CG3_<run_id>.grib2 files found in {grib_dir}")
    LOG.info(f"Found {len(local)} local run(s) in '{grib_dir}'")

    s3: "S3Client" = boto3.client("s3", endpoint_url=args.endpoint_url)
    archived = list_archived(s3, args.bucket, prefix)
    LOG.info(f"Bucket '{args.bucket}/{prefix}' holds {len(archived)} run(s)")

    missing = sorted(set(local) - archived)
    if not missing:
        LOG.info("Nothing to sync; every local run is already in the bucket")
        return
    LOG.info(f"{len(missing)} run(s) to sync: {missing}")

    if args.dry_run:
        LOG.info("Dry run; stopping before upload")
        return

    failures: list[str] = []
    uploaded = 0
    for run_id in missing:
        path = local[run_id]
        key = object_key(prefix, path.name)
        try:
            check_grib2(path)
            digest, size = upload(s3, path, args.bucket, key)
        except (ValueError, ClientError, BotoCoreError, OSError) as e:
            LOG.warning(f"Failed to sync '{path.name}': {e}")
            failures.append(path.name)
            continue

        raw_size = path.stat().st_size
        LOG.info(
            f"Synced '{path.name}' to 's3://{args.bucket}/{key}' "
            f"({raw_size / 1e6:.1f} -> {size / 1e6:.1f} MB gzipped, {size / raw_size:.0%}; sha256 {digest[:12]}…)"
        )
        uploaded += 1

    LOG.info(f"Synced {uploaded} run(s), {len(failures)} failure(s)")
    if failures:
        # NOAA drops runs after ~4 days, so a persistently broken sync is only
        # recoverable for a few days — make it visible in the workflow log.
        raise SystemExit(f"failed to sync: {sorted(failures)}")


if __name__ == "__main__":
    from wavey2.logging import setup_logging

    setup_logging()
    main()
