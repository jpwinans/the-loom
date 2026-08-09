# 0002. FalkorDB server-side ACL for store protection

## Status

Accepted

## Context

On 2026-08-09 an agent's `FLUSHALL` against the shared FalkorDB instance
destroyed every graph on the server, including the production graph
`storehouse`. The `--save 60 1` snapshot cycle then overwrote the only RDB
copy within a minute, so there was no backup to restore from (TL-502).

Nothing in the store's client-side surface prevented this: `theloom/`'s own
code never sends `FLUSHALL`, but any process that can open a Redis
connection to the instance — an agent's ad-hoc `redis-cli`, a misconfigured
script, a library issuing a stray admin command — could. Fixing this by
convention (documentation, code review, "don't do that") is not a fix;
CLAUDE.md invariant 1 makes FalkorDB the single transactional store for
graph, vectors, chunks, and full-text, so there is exactly one place to put
a real guardrail: the server itself, at the protocol level, via Redis ACL.
The goal is mechanism, not policy — refusing catastrophic commands
server-side regardless of which client sends them, while changing nothing
about the CLI's actual operating surface.

## Decision

`users.acl` (repo root, committed — the deny rules are not secret and the
repo is public) defines a restricted `default` user. `default` stays `on
nopass ~* &* +@all` — this remains local-first, and the goal is refusing
catastrophic commands, not adding auth friction — **minus** an explicit,
closed deny list. Everything not named in that list stays allowed under
`+@all`. `docker-compose.yml` mounts the file read-only and wires it in
(see the entrypoint finding below for why that wiring isn't where the
obvious place would suggest). No admin user was added; nothing in rehearsal
proved one necessary (see Consequences).

### Command disposition table (closed set)

Every command family named in the TL-502 task scope, keep/deny, one-line
rationale. "KEEP" means left allowed under the base `+@all`; "DENY" means an
explicit `-command` (or `-command|subcommand`) entry in `users.acl`.

| Command family | Disposition | Rationale |
|---|---|---|
| `FLUSHALL` | DENY | Wipes every key/graph on the server — the incident itself. |
| `FLUSHDB` | DENY | Wipes the current logical DB; same blast radius as `FLUSHALL` in this single-DB deployment. |
| `CONFIG` (`GET`/`SET`/`REWRITE`/`RESETSTAT`) | DENY | Server-wide reconfiguration (persistence, memory policy, auth). Distinct ACL command name from `GRAPH.CONFIG` — verified in rehearsal that denying it leaves `GRAPH.CONFIG` untouched. |
| `GRAPH.CONFIG` | **KEEP** | `tests/conftest.py`'s `small_resultset_cap` fixture (`db.config_get`/`config_set`, which falkordb-py implements as `GRAPH.CONFIG GET`/`SET`) requires it — it's the race-proof guard around `RESULTSET_SIZE`. This is the CLI's actual tuning surface, not the Redis-level one. Residual risk noted below. |
| `ACL SETUSER` / `DELUSER` / `LOAD` / `SAVE` | DENY | The re-grant path — without this, a client that still has `+@all` could run `ACL SETUSER default +flushall` and undo every other row in this table. |
| `ACL GETUSER` / `LIST` / `WHOAMI` / `CAT` / others | **KEEP** | Read-only introspection. The rehearsal's probe ladder depends on `ACL GETUSER default` succeeding to display the deny rules. |
| `SHUTDOWN` | DENY | Kills the server; no legitimate client operation needs it. |
| `DEBUG` | DENY | `DEBUG RELOAD` can discard writes made since the last save. Also currently refused at the `redis-server` config layer by default (`enable-debug-command` unset → "no", confirmed in rehearsal — see Rehearsal evidence), so the ACL denial is belt-and-suspenders and holds even if that config default is ever loosened. |
| `EVAL` / `EVAL_RO` | DENY | Ad-hoc Lua; nothing in the app or the test suite sends raw `EVAL`. |
| `EVALSHA` | **KEEP** | redis-py's `Redis.lock()` — used by `tests/conftest.py`'s `small_resultset_cap` fixture, the cross-process guard around concurrent `RESULTSET_SIZE` mutation — releases/extends/reacquires its lock via a registered Lua script invoked as `EVALSHA`. Denying it breaks that fixture; confirmed by rehearsal (see below). |
| `EVALSHA_RO` | DENY | Unused read-only variant; the `Lock` class only ever calls plain `EVALSHA`. |
| `SCRIPT` (`LOAD`/`EXISTS`/`FLUSH`/`KILL`) | **KEEP** | `EVALSHA` only works once the script is cached server-side; redis-py's `Script` wrapper falls back to `SCRIPT LOAD` on a cache miss (`NoScriptError`) — exactly what happens on every fresh or restarted server. Denying `SCRIPT` would make the `EVALSHA` dependency above unusable in practice. |
| `FUNCTION` / `FCALL` / `FCALL_RO` | DENY | The Redis Functions subsystem; wholly unused by the app. |
| `MODULE` | DENY | Loading/unloading modules, including the graph engine module itself. |
| `REPLICAOF` / `SLAVEOF` | DENY | A rogue `REPLICAOF` could turn this instance into a replica of an attacker-controlled server and overwrite its dataset on the next sync — a slower-motion version of the same incident. |
| `FAILOVER` | DENY | Replication/cluster failover control; no legitimate use in this standalone deployment. |
| `SWAPDB` | DENY | Swaps two logical databases wholesale — same blast radius as `FLUSHDB` through a different door. |
| `MIGRATE` | DENY | Deletes its source key on a successful transfer to another server; not used by the app, and would be a targeted exfiltrate-and-delete primitive if reachable. |
| `RESTORE` | DENY | Writes attacker-supplied serialized bytes into any key this user can touch; not used by the app. |
| `CLUSTER` | DENY | Standalone instance; unused, and subcommands like `CLUSTER RESET`/`FAILOVER` have no legitimate purpose here. |
| `BGSAVE` / `SAVE` / `BGREWRITEAOF` | **KEEP** | Persistence must keep working. The `--save 60 1` automatic snapshot cycle doesn't go through the ACL at all (it's server-internal), but `BGSAVE` stays reachable for any future manual or backup-script trigger. Verified in rehearsal (`BGSAVE` succeeds). |
| `DEL` / `UNLINK` / `EXPIRE` | **KEEP** | Ordinary scoped key operations the app already depends on: `tests/conftest.py`'s per-test namespace teardown (`redis_client.delete(*leftovers)`), `theloom/store/bridges.py`'s migration-claim cleanup, the event log's housekeeping. These only ever touch named keys the app itself manages — a different, non-catastrophic blast radius than `FLUSHALL`/`FLUSHDB`. `GRAPH.DELETE` (below) is FalkorDB's own graph-drop primitive and is a separate ACL command name — it doesn't route through `DEL`. |
| `GRAPH.*` (`QUERY`, `RO_QUERY`, `DELETE`, `LIST`, `COPY`, `EXPLAIN`, `PROFILE`, `SLOWLOG`, `CONSTRAINT`, `CONFIG`, `DB.INDEXES`, `DB.CONSTRAINTS`) | **KEEP** | The CLI's entire operating surface — falkordb-py issues these for every store operation. `GRAPH.DELETE` is the legitimate, scoped way the app removes a graph (the `delete-graph` command); nothing in this family is server-wide the way `FLUSHALL`/`FLUSHDB` are. |
| `CLIENT` / `INFO` / `DBSIZE` / `SCAN` / `KEYS` / `PING` / `ECHO` | **KEEP** | Connection bookkeeping (the client libraries issue `CLIENT SETINFO` on connect) and introspection the CLI, the test suite, and an external watchdog process need. None destroy or reconfigure anything. |

## Rehearsal evidence

All rehearsal ran against a throwaway `tl502-scratch-acl` container (own
volume `tl502-acl-vol`), never against the live `theloom-falkordb`
container. Full transcripts are recorded in the round's report; summarized
here as the findings that shaped the table and files above.

- **Probe ladder** (`docker exec tl502-scratch-acl redis-cli ...`):
  `ACL GETUSER default` displays the deny list; `CONFIG GET maxmemory`,
  `FLUSHALL`, and `FLUSHDB` each return `NOPERM`.
- **Allowed side**: `PING`, `INFO server`, `DBSIZE`, `GRAPH.QUERY`,
  `GRAPH.LIST`, `GRAPH.CONFIG SET RESULTSET_SIZE` (both directions),
  `GRAPH.DELETE`, and `BGSAVE` all succeed.
- **Deny list, individually verified**: `ACL SETUSER default +flushall`,
  `MODULE LIST`, `SHUTDOWN NOSAVE`, `DEBUG SLEEP 0`, `CLUSTER INFO`,
  `MIGRATE`, `RESTORE`, `REPLICAOF NO ONE`, `SLAVEOF NO ONE`,
  `FAILOVER ABORT`, `SWAPDB 0 1`, `FUNCTION LIST`, `FCALL`, and plain
  `EVAL` each return `NOPERM` (`DEBUG` returns its own
  "command not allowed" error, from the server config layer, before ACL is
  even consulted).
- **Script-inherits-ACL**: `SCRIPT LOAD`ing `return redis.call('flushall')`
  and then calling `EVALSHA` on it returns `ERR ACL failure in script: User
  default has no permissions to run the 'flushall' command` — confirming
  that leaving `EVALSHA`/`SCRIPT` allowed does not let a script reach a
  command denied elsewhere in this table; Redis enforces the invoking
  user's ACL for every `redis.call()` inside the script, not just for the
  top-level `EVALSHA`.
- **Test suite**: `GRAPH_PORT=6479 uv run pytest` against the ACL'd
  scratch server: `2179 passed, 5 skipped, 1 warning in 116.01s`. No
  failures — in particular, `small_resultset_cap` and its dependents pass,
  confirming the `EVALSHA`/`SCRIPT` KEEP decision above is both necessary
  and sufficient.
- **Browser UI**: `curl -s -o /dev/null -w "%{http_code}" http://<host>:<port>/`
  against the container's mapped browser port returns `200`, before and
  after the full graph-operation sequence above. (The task-suggested host
  port 3001 intermittently returned 404 during this rehearsal; traced to an
  unrelated local process — a `Code Helper` instance — already listening on
  `127.0.0.1:3001`/`[::1]:3001` on the development host, confirmed via
  `lsof`. Re-run on an unused host port (`3098`) returned `200`
  consistently, including immediately after container start and after
  every subsequent Redis command. This is a shared-host port collision in
  the rehearsal harness, not a defect in the ACL or compose changes —
  `docker-compose.yml` itself keeps the production port `3000:3000`, which
  was never involved.)
- **Entrypoint**: `falkordb/falkordb:latest`'s entrypoint
  (`/var/lib/falkordb/bin/run.sh`) does not reference the container's
  `CMD`/`"$@"` anywhere — it hardcodes
  `exec redis-server ${REDIS_ARGS} --protected-mode no --dir
  "${FALKORDB_DATA_PATH}" --loadmodule "${FALKORDB_BIN_PATH}/falkordb.so"
  ${FALKORDB_ARGS}`. Verified with `docker run ... falkordb/falkordb:latest
  --save 60 1`: `CONFIG GET save` came back as the compiled-in default
  (`3600 1 300 100 60 10000`), not `60 1`. Re-run with
  `-e REDIS_ARGS="--save 60 1"` instead: `CONFIG GET save` returned
  `60 1` as expected. `--aclfile` behaves identically — it has to go
  through `REDIS_ARGS`, not the `command:` array, or it's silently
  ignored and the server starts with no ACL at all despite the file being
  present and mounted. `docker-compose.yml` and the rehearsal `docker run`
  command in the task brief were both written assuming the `command:`
  array reaches `redis-server`; it does not, on this image. This ADR's
  `docker-compose.yml` change routes `--aclfile` through `REDIS_ARGS`
  instead and leaves `command: ["--save", "60", "1"]` untouched (see
  Consequences for what that implies about the existing `--save` line).
- **ACL file syntax**: the first draft of `users.acl` used `#`-prefixed
  comment lines and blank lines for documentation. `redis-server` refused
  to start: `Aborting Redis startup because of ACL errors: .../users.acl:1
  should start with user keyword followed by the username` (repeated per
  comment line). Blank lines alone are tolerated; `#` comments are not —
  every non-blank line in an `--aclfile` must be a `user ...` directive.
  `users.acl` is therefore a single `user default ...` line with no
  header; the rationale that would normally be a file comment lives in
  this ADR instead.

### Alternatives considered and rejected

- **`-@dangerous` (or another built-in category) instead of an explicit
  per-command deny list.** Redis's `@dangerous` category bundles in
  commands this deployment needs kept allowed (e.g. `KEYS`, which the
  task's own KEEP list requires for tooling/watchdog use), and category
  membership is a Redis-version-defined boundary this document doesn't
  control. An explicit, closed list is legible per-command and matches
  what's actually needed.
- **A password or richer auth boundary for `default`.** Out of scope: the
  task is refusing catastrophic commands, not adding auth friction, and a
  password would require every client (the CLI, the test suite, the
  browser) to carry a credential — a much larger blast radius than TL-502
  calls for.
- **A dedicated admin user carrying the denied permissions, for
  maintenance.** Rejected — rehearsal never hit an operation that was
  impossible without one: the full command-disposition table above, the
  probe ladder, the allowed-side checks, and the ~2180-test suite all
  passed under the restricted `default` user alone. If a genuine
  maintenance need appears later, the break-glass procedure documented in
  `SECURITY.md` (a compose override omitting `--aclfile`, requiring host
  access) is the sanctioned path — not a standing elevated credential.
- **Denying `EVAL`/`EVALSHA`/`SCRIPT` wholesale (blocking all Lua).**
  Rejected once rehearsal showed it breaks
  `tests/conftest.py`'s `small_resultset_cap` fixture. Kept `EVALSHA` and
  `SCRIPT` allowed instead, after verifying (script-inherits-ACL, above)
  that doing so grants no command capability beyond what's already listed
  KEEP in the table — the residual exposure is scoped to compute abuse and
  atomic composition of already-allowed commands, not a new destructive
  primitive.

## Consequences

- **`GRAPH.CONFIG` stays reachable.** A client can still change the graph
  engine's server-wide tuning (`RESULTSET_SIZE`, query timeouts, thread
  pool sizing, etc.) without restriction. Accepted because the test
  suite's race-guard fixture needs it and because — unlike the Redis-level
  `CONFIG` this ADR denies — it can't touch persistence, memory eviction,
  or authentication. It is a real, live residual risk, not a closed one.
- **`EVALSHA` and `SCRIPT` stay reachable**, meaning Lua execution is
  technically available to any client. Mitigated, not eliminated: Redis
  enforces the invoking user's ACL inside a script's `redis.call()`s
  (verified in rehearsal), so a script cannot reach anything this table
  denies. The residual exposure is compute abuse (a long-running or
  looping script) and atomic multi-command composition, bounded by the
  same command set already allowed elsewhere in this table.
- **`docker-compose.yml`'s `command: ["--save", "60", "1"]` was already
  dead configuration before this change**, and remains so — the rehearsal
  finding above shows the image's entrypoint never reads it. This ADR
  does not fix that (out of scope for TL-502: it's a pre-existing
  persistence-cadence gap, not a store-protection one), but it does avoid
  repeating the mistake for the new `--aclfile` flag by routing it through
  `REDIS_ARGS`, the channel the entrypoint actually honors. Whether to
  also fix `--save`'s delivery (so the intended 60-second/1-change cadence
  actually takes effect, rather than the compiled-in
  `3600 1 300 100 60 10000` default) is a follow-up worth its own change,
  outside this ADR's scope.
- **No admin user exists.** If a future maintenance operation genuinely
  needs a command this ACL denies, use the break-glass procedure in
  `SECURITY.md` rather than adding a standing credential; if that becomes
  a recurring need, revisit this ADR rather than quietly widening
  `users.acl`.
- **`users.acl` carries no inline documentation** (aclfile syntax forbids
  comments — see Rehearsal evidence). Anyone auditing the deny list should
  read it alongside this ADR's disposition table, not expect the file to
  explain itself.
