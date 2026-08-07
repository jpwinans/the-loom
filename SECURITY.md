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

## Supported versions

Security fixes target the `main` branch.
