# Operations

Day-2 operational tasks for a local FalkorDB-backed Loom store: backing it
up on a schedule, and (eventually) restoring from a backup.

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

_To be added by a sibling change: a tested restore script that reverses
this process (stop the store, replace `dump.rdb` from a chosen backup,
restart, verify)._
