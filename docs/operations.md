# Operations

Day-2 operational tasks for a local FalkorDB-backed Loom store: backing it
up on a schedule, and restoring from a backup.

## Backups

### Why

The store (`theloom-falkordb`, see `docker-compose.yml`) persists to a
single RDB snapshot at `/var/lib/falkordb/data/dump.rdb`, written on the
container's own periodic cycle (`--save 60 1`). That cycle protects against
a process crash, but not against a destructive command against the live
store (a stray `FLUSHALL`/`FLUSHDB`, an accidental `docker volume rm`, a bad
migration): the very next periodic save overwrites `dump.rdb` with the
now-empty state, and there is no second copy anywhere. `scripts/backup_store.py`
is that second copy, taken on demand or on a schedule, kept out of the repo
and out of the container's own volume.

### What `scripts/backup_store.py` does

1. Connects to FalkorDB using the same config path as the rest of the CLI
   (`theloom.config.load_config()` — architecture invariant 5), so
   `GRAPH_HOST`/`GRAPH_PORT`/`~/.loom/config.json` all apply exactly as they
   do to `loom` itself.
2. Triggers `BGSAVE` (a non-destructive, allowed persistence command) and
   polls `LASTSAVE` until it advances past its pre-trigger value, with a
   timeout — this is how the script knows the snapshot it is about to copy
   actually reflects the data at the moment the backup ran, not a stale one
   left over from the last periodic save.
3. Copies the resulting `dump.rdb` out of the container at the docker level
   (`docker cp <container>:/var/lib/falkordb/data/dump.rdb <dest>/dump-<timestamp>.rdb`) —
   see [Dump naming and location](#dump-naming-and-location) below for the
   exact contract.
4. Rotates: deletes the oldest backups beyond `--keep`, matching only its
   own `dump-*.rdb` naming pattern — it never touches any other file that
   happens to live in `--dest`.

On any failure (container not running, BGSAVE timeout, copy failure) it
exits non-zero with a message on stderr; on success it prints the path of
the backup file it created.

### Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--container` | `theloom-falkordb` | Name of the running FalkorDB container to back up. |
| `--dest` | `~/.loom/backups/` | Destination directory for backup files, created if missing. Deliberately outside the repo (which lives in Dropbox) so backups are never synced, committed, or swept up by repo tooling. |
| `--keep` | `7` | Number of `dump-*.rdb` backups to retain after rotation. |

Connection host/port are not flags — they come from config
(`GRAPH_HOST`/`GRAPH_PORT` env vars, or `~/.loom/config.json`), consistent
with every other command against the store.

### Dump naming and location

- **Filename pattern**: `dump-YYYYMMDD-HHMMSS.rdb` (local time,
  zero-padded, so lexical sort order is chronological order).
- **Default location**: `~/.loom/backups/` (override with `--dest`).
- Rotation, and any tooling built on top of these backups, identifies "ours"
  by this exact glob: `dump-*.rdb`. Nothing else in the destination
  directory is ever read, counted, or deleted by this script.

### Running it manually

```bash
uv run python scripts/backup_store.py
# -> /Users/jameswinans/.loom/backups/dump-20260809-133615.rdb
```

### Scheduling recipe (launchd, macOS)

The script does not install anything itself — no cron entry, no launchd
job. Scheduling is opt-in and manual. The following `launchd` plist runs a
daily backup; adjust the `uv` path (`which uv`) and repository path for your
machine before using it, and note the working directory matters (`uv run`
resolves the project from cwd).

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.theloom.backup</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/jameswinans/.local/bin/uv</string>
        <string>run</string>
        <string>python</string>
        <string>scripts/backup_store.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/jameswinans/Dropbox/Development/the-loom</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>3</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/jameswinans/.loom/backups/backup.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/jameswinans/.loom/backups/backup.err.log</string>
</dict>
</plist>
```

This example is **not auto-installed** by anything in this repo. To use it,
a human saves it as (for example)
`~/Library/LaunchAgents/com.theloom.backup.plist` and loads it explicitly:

```bash
launchctl load ~/Library/LaunchAgents/com.theloom.backup.plist
```

and unloads it the same way (`launchctl unload ...`) to stop the schedule.
`StartCalendarInterval` with only `Hour`/`Minute` set runs once a day at
that time; `launchd` also catches up a missed run (e.g. the machine was
asleep) the next time it wakes, unlike `cron`.

## Restore

### Why file-level, not the redis `RESTORE` command

`users.acl` denies the redis `RESTORE` command (it deserializes
attacker-supplied bytes into a single key and the app never uses it -- see
[adr/0002-falkordb-acl-store-protection.md](adr/0002-falkordb-acl-store-protection.md)).
That denial is irrelevant here: restoring a backup means putting a whole
snapshot back, not one key, so `scripts/restore_store.py` never sends
`RESTORE` (or any other denied command) over the wire. It only does
docker-level operations (`docker stop` / `docker cp` / `docker start`) plus
`PING` and `GRAPH.LIST` against the restarted server -- both allowed for the
restricted `default` user. **Restore needs no ACL exception, no break-glass
override, and no change to `docker-compose.yml`.** It works exactly the same
whether or not the target is running the shipped ACL'd config.

### What `scripts/restore_store.py` does

1. Resolves which backup to restore: either the file passed via `--dump`,
   or, with `--latest`, the lexically-last `dump-*.rdb` in `--source-dir`
   (default `~/.loom/backups/`, matching `backup_store.py`'s default
   destination and `dump-*.rdb` naming contract).
2. **Without `--yes`**, prints a dry-run summary -- target container,
   resolved dump path, that file's mtime and size -- and exits non-zero
   without touching anything. This is the confirmation gate: restore is
   destructive, so nothing happens by accident.
3. **With `--yes`**, after confirming the dump file and target container
   both exist:
   - `docker stop <target>`
   - `docker cp <dump> <target>:/var/lib/falkordb/data/dump.rdb` (the same
     in-container path `backup_store.py` copies out of, see
     [Dump naming and location](#dump-naming-and-location))
   - `docker start <target>`
   - polls `PING` until the restarted server answers, bounded by `--timeout`
     (default 30s)
   - prints `GRAPH.LIST` of the restored store
   - exits 0

If any step from `docker stop` onward fails, the script reports the failure
on stderr and exits non-zero. Because the target may be left stopped at that
point, it makes one best-effort attempt to start it again before exiting,
and reports separately on stderr if that recovery attempt also fails. Every
`docker` step is itself bounded at 120 seconds (`_DOCKER_TIMEOUT_SECONDS`),
so a wedged restore (a hung `docker cp`, a stuck daemon) self-terminates in
~120s rather than hanging forever -- ~240s worst case, if the recovery
restart also hangs -- with the recovery-restart attempt still firing on the
way out; an operator never needs to kill a hung restore by hand.
Failures before `docker stop` (missing dump file, unreadable dump file,
nonexistent target container) never touch the target at all.

### Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--container` | _(required, no default)_ | Target FalkorDB container to restore into. Restore overwrites the target's entire dataset, so the victim must always be named explicitly. |
| `--dump` | -- | Path to a specific `dump-*.rdb` file. Mutually exclusive with `--latest`; exactly one is required. |
| `--latest` | -- | Restore the lexically-last `dump-*.rdb` file in `--source-dir`. Mutually exclusive with `--dump`. |
| `--source-dir` | `~/.loom/backups/` | Directory searched when using `--latest`. |
| `--yes` | off | Actually perform the restore. Without it, only the dry-run preview runs. |
| `--timeout` | `30` | Seconds to wait for the target to answer `PING` after restart. |

Connection host/port for the post-restore `PING`/`GRAPH.LIST` checks are not
flags -- like `backup_store.py`, they come from
`theloom.config.load_config()` (`GRAPH_HOST`/`GRAPH_PORT` env vars, or
`~/.loom/config.json`), which is how a rehearsal run points the checks at a
scratch container instead of the real store.

### Running it manually (rehearsal)

```bash
uv run python scripts/restore_store.py --container my-scratch-falkordb --latest
# dry run (no --yes): prints target/dump/mtime/size, exits non-zero, touches nothing

uv run python scripts/restore_store.py --container my-scratch-falkordb --latest --yes
# stops the container, replaces dump.rdb, restarts it, waits for PING, prints GRAPH.LIST
```

### The live recovery procedure

This is the documented procedure for restoring the real store
(`theloom-falkordb`) from the most recent backup. **It is a last resort** --
run it only when the live store's data is already gone or known-bad (a
destructive command got through, a bad migration, volume loss); restoring
overwrites everything currently in the container with whatever `dump.rdb`
was captured at backup time, discarding any writes made since.

1. Confirm the incident: what happened, and that restoring is the right
   response (not, say, a transient connection issue).
2. From the main checkout (not a worktree -- this touches the real
   container), inspect the available backups: `ls -la ~/.loom/backups/`.
3. Run the restore against the real container, from `~/.loom/backups/`
   (both defaults, so no `--container` other than the real name and no
   `--source-dir` override are needed beyond naming the target):

   ```bash
   uv run python scripts/restore_store.py --container theloom-falkordb --latest
   ```

   First **without** `--yes` -- read the dry-run output and confirm the
   dump file it picked (path, mtime, size) is the one intended. Only then
   re-run the identical command with `--yes` appended.
4. Confirm recovery: the command's own `GRAPH.LIST` output at the end shows
   the expected graphs; spot-check with `uv run loom graph-stats
   '{"graph": "<name>"}'` against a graph known to matter (e.g. the
   production default graph).
5. Note in the incident record which backup was restored and what, if
   anything, was lost (any write between that backup's timestamp and the
   incident is gone).
