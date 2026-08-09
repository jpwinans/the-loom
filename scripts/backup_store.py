"""Take one rotating on-disk backup of the FalkorDB store's RDB snapshot.

Context: the 2026-08-09 FLUSHALL incident destroyed every graph, and the
`--save 60 1` snapshot cycle in docker-compose.yml overwrote the only
dump.rdb copy within a minute -- there was no second copy anywhere. This
script is the durable half of the fix: it forces a fresh, complete snapshot
(BGSAVE, not the periodic cycle) and copies it out to a directory the
container's own snapshotting can never touch, keeping a bounded number of
prior copies.

Run: `uv run python scripts/backup_store.py` (uses the running
`theloom-falkordb` container and writes to `~/.loom/backups/`, both
overridable -- see `--help`). Connection host/port resolve through
`theloom.config.load_config()`, the single config path (architecture
invariant 5) shared with the rest of the CLI -- `GRAPH_HOST`/`GRAPH_PORT`
redirect it, which is how a rehearsal run points this at a scratch
container instead of the real store.

Mechanics: BGSAVE is a non-destructive, allowed persistence command (unlike
FLUSHALL/FLUSHDB/CONFIG/SCRIPT FLUSH, which this script and everything else
touching FalkorDB must never call). It is asynchronous, so completion is
detected by polling LASTSAVE until it advances past its pre-trigger value,
with a timeout. The resulting dump.rdb is then copied out at the docker
level -- `docker cp <container>:/var/lib/falkordb/data/dump.rdb
<dest>/dump-YYYYMMDD-HHMMSS.rdb` -- because that in-container path is where
the image always writes its RDB (see docker-compose.yml); the client
connection is only ever used to trigger and await the save, never to read
data. `--dest` defaults outside the repo (which lives in Dropbox) so
backups are never synced, committed, or pruned by anything else.

Rotation only ever deletes files matching this script's own `dump-*.rdb`
naming pattern in `--dest`, oldest first, keeping the newest `--keep`; it
never touches any other file a caller may have placed there.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from falkordb import FalkorDB

from theloom.config import credential_kwargs, load_config

# The falkordb/falkordb image always writes its RDB snapshot here inside the
# container (see docker-compose.yml) -- a load-bearing repo fact, not
# something this script derives or guesses.
_DUMP_PATH_IN_CONTAINER = "/var/lib/falkordb/data/dump.rdb"

_DEFAULT_CONTAINER = "theloom-falkordb"
_DEFAULT_DEST = Path.home() / ".loom" / "backups"
_DEFAULT_KEEP = 7

# Rotation and any future restore tooling identify "ours" by this exact
# pattern -- nothing else in --dest is ever counted, listed, or deleted.
_BACKUP_GLOB = "dump-*.rdb"
_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"

_BGSAVE_TIMEOUT_SECONDS = 60.0
_BGSAVE_POLL_SECONDS = 0.5


def _fail(message: str) -> None:
    sys.stderr.write(f"backup_store: {message}\n")
    sys.exit(1)


def _container_running(container: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", container],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _wait_for_bgsave(connection: Any) -> None:
    """Trigger BGSAVE and block until LASTSAVE advances past its prior value."""
    try:
        before = connection.lastsave()
        connection.bgsave()
    except Exception as exc:
        # Any client/connection failure here is a script failure, not a
        # traceback: report it on stderr with the same non-zero exit as
        # every other failure mode.
        _fail(f"BGSAVE could not be triggered: {exc}")
        return  # unreachable; _fail exits, but satisfies type-narrowing

    deadline = time.monotonic() + _BGSAVE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if connection.lastsave() > before:
            return
        time.sleep(_BGSAVE_POLL_SECONDS)
    _fail(
        f"BGSAVE did not complete within {_BGSAVE_TIMEOUT_SECONDS:.0f}s "
        f"(LASTSAVE never advanced past {before.isoformat()})."
    )


def _copy_dump(container: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime(_TIMESTAMP_FORMAT)
    target = dest / f"dump-{timestamp}.rdb"
    result = subprocess.run(
        ["docker", "cp", f"{container}:{_DUMP_PATH_IN_CONTAINER}", str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _fail(f"docker cp failed: {result.stderr.strip()}")
    return target


def _rotate(dest: Path, keep: int) -> None:
    """Delete the oldest dump-*.rdb backups in dest beyond the newest `keep`."""
    backups = sorted(dest.glob(_BACKUP_GLOB), key=lambda p: p.name)
    for old in backups[: max(0, len(backups) - keep)]:
        old.unlink()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--container",
        default=_DEFAULT_CONTAINER,
        help=f"FalkorDB container name (default: {_DEFAULT_CONTAINER}).",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=_DEFAULT_DEST,
        help=f"Backup destination directory, created if missing (default: {_DEFAULT_DEST}).",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=_DEFAULT_KEEP,
        help=f"Number of dump-*.rdb backups to retain (default: {_DEFAULT_KEEP}).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if not _container_running(args.container):
        _fail(f"container {args.container!r} is not running (or does not exist).")

    config = load_config()
    db = FalkorDB(host=config.host, port=config.port, **credential_kwargs(config))

    _wait_for_bgsave(db.connection)
    target = _copy_dump(args.container, args.dest)
    _rotate(args.dest, args.keep)

    print(target)


if __name__ == "__main__":
    main()
