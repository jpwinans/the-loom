# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately via GitHub's
[security advisories](https://github.com/jpwinans/the-loom/security/advisories/new)
rather than opening a public issue. Reports are acknowledged as quickly as
possible, and fixes land on `main` before any public disclosure.

## Scope

The Loom is a local-first CLI. It assumes a trusted FalkorDB instance on a
private interface, and `loom serve` binds a read-only API to localhost with no
authentication — do not expose either to untrusted networks. Reports about
deployments that ignore those boundaries are out of scope; anything that breaks
them from *within* the documented setup (for example, an input that mutates the
store through the read-only server) is in scope.

Within that trusted-instance assumption, the FalkorDB server itself refuses
the commands that can destroy or reconfigure the whole instance, at the ACL
layer — mechanism, not just documented policy. `docker-compose.yml` mounts
`users.acl` (repo root) into the container and points `redis-server` at it;
the restricted `default` user keeps `+@all` (this is still local-first, not
an auth boundary) minus an explicit deny list covering `FLUSHALL`/`FLUSHDB`,
`CONFIG`, the `ACL` subcommands that could re-grant what's denied here,
`SHUTDOWN`, `DEBUG`, ad-hoc scripting (`EVAL`/`FUNCTION`/`FCALL`),
`MODULE`, replication (`REPLICAOF`/`SLAVEOF`/`FAILOVER`), `SWAPDB`,
`MIGRATE`, `RESTORE`, and `CLUSTER`. `GRAPH.*` (including `GRAPH.CONFIG`)
stays allowed — it's the CLI's normal operating surface. The full
command-disposition table, the rehearsal evidence behind it, and the
break-glass procedure below are in
[docs/adr/0002-falkordb-acl-store-protection.md](docs/adr/0002-falkordb-acl-store-protection.md).

**Break-glass:** if a maintenance operation genuinely needs a command this
ACL denies, do not edit `users.acl` in place on a running production
instance. Bring the server up with a compose override that omits the
`--aclfile` line from `REDIS_ARGS` (an override file, not a change to the
committed `docker-compose.yml`) — this requires host access to create and
apply, which is the point: the store returns to its old, unrestricted
posture only through a deliberate, visible, host-level action, never through
a command sent over the wire. Revert by removing the override and
restarting.

## Supported versions

Security fixes target the `main` branch.
