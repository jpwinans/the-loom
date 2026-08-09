"""File-level restore of a FalkorDB RDB snapshot into a named container.

Context: `scripts/backup_store.py` is the durable half of the TL-502 fix (a
second copy of `dump.rdb`, safe from both the container's own snapshot cycle
and a stray `FLUSHALL`); this script is the other half -- turning one of
those backups back into a live store. It is deliberately dumb: it never
speaks the redis `RESTORE` command (which deserializes attacker-controlled
bytes into a single key and stays denied by `users.acl` -- see
`docs/adr/0002-falkordb-acl-store-protection.md`). Instead it replaces the
whole `dump.rdb` file at the docker level while the container is stopped,
which needs no ACL exception at all: nothing this script sends over the
wire is anything the restricted `default` user can't already do (`PING`,
`GRAPH.LIST`). That makes this script safe to point at a store running the
shipped, ACL'd `docker-compose.yml` without any break-glass override.

Run: `uv run python scripts/restore_store.py --container <name> --latest`
(or `--dump <path>` for a specific backup). `--container` has no default --
restore overwrites the target's entire dataset, so the script refuses to
guess a victim. Connection host/port for the post-restore PING/GRAPH.LIST
checks resolve through `theloom.config.load_config()` (architecture
invariant 5), same as `backup_store.py` -- `GRAPH_HOST`/`GRAPH_PORT`
redirect them at a scratch container during rehearsal instead of the real
store.

Safety gate: without `--yes`, the script only prints what it *would* do
(target container, resolved dump file, that file's mtime/size) and exits
non-zero -- it touches nothing. Only `--yes` performs the destructive
sequence: `docker stop <target>` -> `docker cp <dump>
<target>:/var/lib/falkordb/data/dump.rdb` -> `docker start <target>` ->
poll `PING` until the server is back up (bounded timeout) -> print
`GRAPH.LIST` of the restored store. If any step from `docker stop` onward
fails, the script reports the failure on stderr, exits non-zero, and -- since
the target may now be sitting stopped -- makes one best-effort attempt to
start it again before exiting, reporting separately if that also fails.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import NoReturn

from falkordb import FalkorDB

from theloom.config import credential_kwargs, load_config

# Same load-bearing fact backup_store.py relies on: the falkordb/falkordb
# image always writes (and expects to read) its RDB snapshot here inside the
# container. See docker-compose.yml.
_DUMP_PATH_IN_CONTAINER = "/var/lib/falkordb/data/dump.rdb"

_DEFAULT_SOURCE_DIR = Path.home() / ".loom" / "backups"

# Must match backup_store.py's rotation glob exactly -- this is how --latest
# picks "ours" out of a directory that may hold other files.
_BACKUP_GLOB = "dump-*.rdb"
_MTIME_DISPLAY_FORMAT = "%Y-%m-%d %H:%M:%S"

_READY_TIMEOUT_SECONDS = 30.0
_READY_POLL_SECONDS = 0.5


def _fail(message: str) -> NoReturn:
    sys.stderr.write(f"restore_store: {message}\n")
    sys.exit(1)


class _RestoreFailure(Exception):
    """A restore step failed after the target may have been stopped.

    Carries a stderr-ready message; raised instead of exiting directly so
    ``main`` gets a chance to attempt recovery (restarting the target) before
    reporting the failure and exiting non-zero.
    """


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def _resolve_dump_path(dump: Path | None, latest: bool, source_dir: Path) -> Path:
    if latest:
        candidates = sorted(source_dir.glob(_BACKUP_GLOB), key=lambda p: p.name)
        if not candidates:
            _fail(f"no {_BACKUP_GLOB} backups found in {source_dir}")
        return candidates[-1]
    assert dump is not None  # argparse's mutually-exclusive group guarantees this
    if not dump.is_file():
        _fail(f"dump file not found: {dump}")
    return dump


def _verify_container_exists(container: str) -> None:
    result = _run(["docker", "inspect", "--format", "{{.Id}}", container])
    if result.returncode != 0:
        _fail(f"target container {container!r} not found: {result.stderr.strip()}")


def _stop_container(container: str) -> None:
    result = _run(["docker", "stop", container])
    if result.returncode != 0:
        raise _RestoreFailure(f"docker stop {container!r} failed: {result.stderr.strip()}")


def _copy_dump_into_container(dump_path: Path, container: str) -> None:
    result = _run(["docker", "cp", str(dump_path), f"{container}:{_DUMP_PATH_IN_CONTAINER}"])
    if result.returncode != 0:
        raise _RestoreFailure(f"docker cp into {container!r} failed: {result.stderr.strip()}")


def _start_container(container: str) -> None:
    result = _run(["docker", "start", container])
    if result.returncode != 0:
        raise _RestoreFailure(f"docker start {container!r} failed: {result.stderr.strip()}")


def _wait_for_ready(host: str, port: int, creds: dict[str, str], timeout: float) -> FalkorDB:
    """Poll PING (allowed under the ACL) until the restarted server answers.

    The ``FalkorDB`` client connects eagerly in its constructor (it issues an
    ``INFO`` call to detect Sentinel/Cluster topology before returning), so a
    server that is still coming back up after ``docker start`` can make
    *construction itself* raise a connection error -- not just a later
    ``.ping()`` call. Retrying the whole client construction here, not just a
    ping against an already-built client, is what actually rides out that
    startup window.
    """
    deadline = time.monotonic() + timeout
    last_error = "no PING attempt succeeded"
    while time.monotonic() < deadline:
        try:
            db = FalkorDB(host=host, port=port, **creds)
            if db.connection.ping():
                return db
        except Exception as exc:  # noqa: BLE001 - not-yet-listening is expected
            last_error = str(exc)
        time.sleep(_READY_POLL_SECONDS)
    raise _RestoreFailure(f"target did not respond to PING within {timeout:.0f}s ({last_error})")


def _attempt_recovery_restart(container: str) -> None:
    """Best-effort: the target may be sitting stopped after a failed step."""
    result = _run(["docker", "start", container])
    if result.returncode == 0:
        sys.stderr.write(f"restore_store: recovery: restarted {container!r} after failure.\n")
    else:
        sys.stderr.write(
            f"restore_store: recovery FAILED -- {container!r} may still be stopped: "
            f"{result.stderr.strip()}\n"
        )


def _print_dry_run(container: str, dump_path: Path) -> None:
    stat = dump_path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime).strftime(_MTIME_DISPLAY_FORMAT)
    print("restore_store: DRY RUN -- pass --yes to actually restore. Nothing was touched.")
    print(f"  target container : {container}")
    print(f"  dump file        : {dump_path}")
    print(f"  dump mtime       : {mtime}")
    print(f"  dump size        : {stat.st_size} bytes")
    print(
        f"Would: docker stop {container} -> replace its dump.rdb with this file "
        f"-> docker start {container}."
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--container",
        required=True,
        help=(
            "Target FalkorDB container to restore into. No default -- restore "
            "overwrites the target's entire dataset, so the victim must be named "
            "explicitly."
        ),
    )
    dump_source = parser.add_mutually_exclusive_group(required=True)
    dump_source.add_argument(
        "--dump",
        type=Path,
        help="Path to a specific dump-*.rdb file to restore.",
    )
    dump_source.add_argument(
        "--latest",
        action="store_true",
        help="Restore the lexically-last dump-*.rdb file in --source-dir.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=_DEFAULT_SOURCE_DIR,
        help=f"Directory to search when using --latest (default: {_DEFAULT_SOURCE_DIR}).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Actually perform the restore. Without this flag, the script only "
            "prints what it would do and exits non-zero."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_READY_TIMEOUT_SECONDS,
        help=(
            "Seconds to wait for the target to answer PING after restart "
            f"(default: {_READY_TIMEOUT_SECONDS:.0f})."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    dump_path = _resolve_dump_path(args.dump, args.latest, args.source_dir)

    if not args.yes:
        _print_dry_run(args.container, dump_path)
        sys.exit(1)

    _verify_container_exists(args.container)
    config = load_config()

    stopped = False
    try:
        _stop_container(args.container)
        stopped = True
        _copy_dump_into_container(dump_path, args.container)
        _start_container(args.container)
        db = _wait_for_ready(config.host, config.port, credential_kwargs(config), args.timeout)
        graphs = db.list_graphs()
    except _RestoreFailure as exc:
        if stopped:
            _attempt_recovery_restart(args.container)
        _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - last-resort: no raw traceback on stderr
        if stopped:
            _attempt_recovery_restart(args.container)
        _fail(f"unexpected error: {exc}")

    print(f"restored {dump_path} into {args.container!r}")
    for name in graphs:
        print(name)


if __name__ == "__main__":
    main()
